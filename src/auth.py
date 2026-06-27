"""Authentication & User Management — handles registration, login, and roles.

Supports email and Google sign-in. Users are stored as JSON files under
.grasp_state/users/. New accounts require admin approval before access.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import bcrypt as _bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

logger = logging.getLogger(__name__)

VALID_ROLES = (
    "Intern",
    "Junior Associate",
    "Associate",
    "Senior Associate",
    "Team Lead",
    "Manager",
    "Director",
    "Principal",
    "Vice President",
    "Partner",
)
VALID_STATUSES = ("pending_approval", "approved", "rejected")
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


class UserManager:
    """Manages user accounts stored as JSON files."""

    def __init__(self, state_dir: Path, session_secret: str, google_client_id: str = ""):
        self.users_dir = state_dir / "users"
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.session_secret = session_secret
        self.google_client_id = google_client_id
        self._serializer = URLSafeTimedSerializer(session_secret)

    # ── Persistence ────────────────────────────────────────

    def _user_path(self, user_id: str) -> Path:
        return self.users_dir / f"{user_id}.json"

    def _load(self, user_id: str) -> dict[str, Any] | None:
        path = self._user_path(user_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _save(self, user: dict[str, Any]) -> None:
        path = self._user_path(user["id"])
        path.write_text(json.dumps(user, indent=2), encoding="utf-8")

    def _find_by_email(self, email: str) -> dict[str, Any] | None:
        """Find a user by email (case-insensitive)."""
        email_lower = email.strip().lower()
        for filepath in self.users_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                if data.get("email", "").lower() == email_lower:
                    return data
            except Exception:
                continue
        return None

    # ── Registration ───────────────────────────────────────

    def register_email(
        self,
        first_name: str,
        last_name: str,
        dob: str,
        email: str,
        password: str,
    ) -> dict[str, Any]:
        """Register a new user via email. Returns the user or an error dict."""
        email = email.strip().lower()

        # Check for existing account
        existing = self._find_by_email(email)
        if existing:
            if existing.get("auth_method") == "google":
                return {
                    "error": "This email is already linked to a Google account. Please sign in using Google.",
                    "conflict": "google",
                }
            return {
                "error": "An account with this email already exists. Please sign in.",
                "conflict": "email",
            }

        # Hash the password
        password_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")

        user_id = str(uuid.uuid4())[:12]
        user = {
            "id": user_id,
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "dob": dob,
            "email": email,
            "password_hash": password_hash,
            "auth_method": "email",
            "status": "pending_approval",
            "role": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approved_at": None,
            "google_id": None,
        }

        self._save(user)
        logger.info(f"New email registration: {email} (id={user_id})")
        return {"user": self._public_user(user)}

    async def register_google(self, id_token: str) -> dict[str, Any]:
        """Register/login a user via Google ID token. Returns user or error."""
        token_info = await self._verify_google_token(id_token)
        if "error" in token_info:
            return token_info

        email = token_info["email"].lower()
        given_name = token_info.get("given_name", "")
        family_name = token_info.get("family_name", "")
        google_id = token_info.get("sub", "")

        # Check for existing account
        existing = self._find_by_email(email)
        if existing:
            if existing.get("auth_method") == "email":
                return {
                    "error": "This email is already registered with an email account. Please sign in using your email and password.",
                    "conflict": "email",
                }
            # Already a Google user — treat as login
            if existing.get("status") == "pending_approval":
                return {
                    "user": self._public_user(existing),
                    "pending": True,
                }
            token = self._create_token(existing["id"])
            return {"user": self._public_user(existing), "token": token}

        user_id = str(uuid.uuid4())[:12]
        user = {
            "id": user_id,
            "first_name": given_name,
            "last_name": family_name,
            "dob": "",
            "email": email,
            "password_hash": "",
            "auth_method": "google",
            "status": "pending_approval",
            "role": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approved_at": None,
            "google_id": google_id,
        }

        self._save(user)
        logger.info(f"New Google registration: {email} (id={user_id})")
        return {"user": self._public_user(user)}

    # ── Login ──────────────────────────────────────────────

    def login_email(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate via email + password. Returns user + token or error."""
        user = self._find_by_email(email.strip().lower())
        if not user:
            return {"error": "No account found with this email."}

        if user.get("auth_method") == "google":
            return {
                "error": "This email is linked to a Google account. Please sign in using Google.",
                "conflict": "google",
            }

        if not _bcrypt.checkpw(password.encode("utf-8"), user.get("password_hash", "").encode("utf-8")):
            return {"error": "Incorrect password."}

        if user.get("status") == "pending_approval":
            return {
                "user": self._public_user(user),
                "pending": True,
            }

        if user.get("status") == "rejected":
            return {"error": "Your account has been rejected. Please contact an administrator."}

        token = self._create_token(user["id"])
        return {"user": self._public_user(user), "token": token}

    async def login_google(self, id_token: str) -> dict[str, Any]:
        """Authenticate via Google ID token. Returns user + token or error."""
        token_info = await self._verify_google_token(id_token)
        if "error" in token_info:
            return token_info

        email = token_info["email"].lower()
        user = self._find_by_email(email)

        if not user:
            return {"error": "No account found. Please register first."}

        if user.get("auth_method") == "email":
            return {
                "error": "This email is registered with an email account. Please sign in using your email and password.",
                "conflict": "email",
            }

        if user.get("status") == "pending_approval":
            return {
                "user": self._public_user(user),
                "pending": True,
            }

        if user.get("status") == "rejected":
            return {"error": "Your account has been rejected. Please contact an administrator."}

        token = self._create_token(user["id"])
        return {"user": self._public_user(user), "token": token}

    # ── Session ────────────────────────────────────────────

    def _create_token(self, user_id: str) -> str:
        """Create a signed session token."""
        return self._serializer.dumps({"uid": user_id})

    def verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify a session token and return the user, or None."""
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
            user = self._load(data["uid"])
            if user and user.get("status") == "approved":
                return self._public_user(user)
            return None
        except (BadSignature, SignatureExpired, KeyError):
            return None

    # ── Admin Actions ──────────────────────────────────────

    def approve_user(self, user_id: str, role: str) -> dict[str, Any]:
        """Approve a user and assign a role."""
        user = self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if role not in VALID_ROLES:
            return {"error": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"}

        user["status"] = "approved"
        user["role"] = role
        user["approved_at"] = datetime.now(timezone.utc).isoformat()
        self._save(user)

        logger.info(f"User {user_id} ({user['email']}) approved with role '{role}'")
        return {"user": self._public_user(user), "message": f"User approved as {role}"}

    def reject_user(self, user_id: str) -> dict[str, Any]:
        """Reject a pending user."""
        user = self._load(user_id)
        if not user:
            return {"error": "User not found"}

        user["status"] = "rejected"
        self._save(user)

        logger.info(f"User {user_id} ({user['email']}) rejected")
        return {"message": "User rejected"}

    def update_role(self, user_id: str, new_role: str) -> dict[str, Any]:
        """Change an approved user's role."""
        user = self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if new_role not in VALID_ROLES:
            return {"error": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"}

        old_role = user.get("role")
        user["role"] = new_role
        self._save(user)

        logger.info(f"User {user_id} role changed: {old_role} → {new_role}")
        return {
            "user": self._public_user(user),
            "message": f"Role changed from {old_role} to {new_role}",
        }

    def list_users(self) -> list[dict[str, Any]]:
        """List all registered users."""
        users = []
        for filepath in self.users_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                users.append(self._public_user(data))
            except Exception as e:
                logger.warning(f"Failed to load user {filepath.name}: {e}")
        users.sort(key=lambda u: u.get("created_at", ""), reverse=True)
        return users

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Get a single user by ID (public view)."""
        user = self._load(user_id)
        if user:
            return self._public_user(user)
        return None

    # ── Google Token Verification ──────────────────────────

    async def _verify_google_token(self, id_token: str) -> dict[str, Any]:
        """Verify a Google ID token via Google's tokeninfo endpoint."""
        if not self.google_client_id:
            return {"error": "Google sign-in is not configured on this server."}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": id_token},
                    timeout=10.0,
                )
            if resp.status_code != 200:
                return {"error": "Invalid Google token."}

            info = resp.json()

            # Verify the token was issued for our client
            if info.get("aud") != self.google_client_id:
                return {"error": "Token was not issued for this application."}

            if not info.get("email"):
                return {"error": "Google account has no email."}

            return {
                "email": info["email"],
                "given_name": info.get("given_name", ""),
                "family_name": info.get("family_name", ""),
                "sub": info.get("sub", ""),
            }
        except Exception as e:
            logger.error(f"Google token verification failed: {e}")
            return {"error": "Failed to verify Google token."}

    # ── Helpers ────────────────────────────────────────────

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        """Return a user dict with sensitive fields stripped."""
        return {
            "id": user["id"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "email": user.get("email", ""),
            "auth_method": user.get("auth_method", "email"),
            "status": user.get("status", "pending_approval"),
            "role": user.get("role"),
            "created_at": user.get("created_at", ""),
            "approved_at": user.get("approved_at"),
        }
