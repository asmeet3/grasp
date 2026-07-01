"""Authentication & User Management — handles registration, login, and roles.

Supports email and Google sign-in. Users are stored in PostgreSQL.
New accounts require admin approval before access.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import bcrypt as _bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .database import users_table

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
    """Manages user accounts stored in PostgreSQL."""

    def __init__(self, engine: AsyncEngine, session_secret: str, google_client_id: str = ""):
        self.engine = engine
        self.session_secret = session_secret
        self.google_client_id = google_client_id
        self._serializer = URLSafeTimedSerializer(session_secret)

    # ── Persistence ────────────────────────────────────────

    async def _load(self, user_id: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(users_table).where(users_table.c.id == user_id)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _save(self, user: dict[str, Any]) -> None:
        stmt = pg_insert(users_table).values(**user)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={k: v for k, v in user.items() if k != "id"},
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def _find_by_email(self, email: str) -> dict[str, Any] | None:
        """Find a user by email (case-insensitive)."""
        email_lower = email.strip().lower()
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(users_table).where(
                    func.lower(users_table.c.email) == email_lower
                )
            )
            row = result.mappings().first()
            return dict(row) if row else None

    # ── Registration ───────────────────────────────────────

    async def register_email(
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
        existing = await self._find_by_email(email)
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
            "created_at": datetime.now(timezone.utc),
            "approved_at": None,
            "google_id": None,
        }

        await self._save(user)
        logger.info(f"New email registration: {email} (id={user_id})")
        return {"user": self._public_user(user), "pending": True}

    async def register_google(self, id_token: str) -> dict[str, Any]:
        """Register/login a user via Google ID token. Returns user or error."""
        token_info = await self._verify_google_token(id_token)
        if "error" in token_info:
            return token_info

        email = token_info["email"].lower()
        given_name = token_info.get("given_name", "")
        family_name = token_info.get("family_name", "")
        google_id = token_info.get("sub", "")
        profile_picture = token_info.get("picture", "")

        # Check for existing account
        existing = await self._find_by_email(email)
        if existing:
            if existing.get("auth_method") == "email":
                return {
                    "error": "This email is already registered with an email account. Please sign in using your email and password.",
                    "conflict": "email",
                }
            # Already a Google user — treat as login, refresh profile from Google
            if existing.get("status") == "pending_approval":
                # Still refresh picture/name even for pending users
                await self._refresh_google_profile(existing, given_name, family_name, google_id, profile_picture)
                return {
                    "user": self._public_user(existing),
                    "pending": True,
                }
            await self._refresh_google_profile(existing, given_name, family_name, google_id, profile_picture)
            pv = existing.get("password_version", 0)
            token = self._create_token(existing["id"], pv)
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
            "created_at": datetime.now(timezone.utc),
            "approved_at": None,
            "google_id": google_id,
            "profile_picture": profile_picture,
        }

        await self._save(user)
        logger.info(f"New Google registration: {email} (id={user_id})")
        return {"user": self._public_user(user), "pending": True}

    # ── Login ──────────────────────────────────────────────

    async def login_email(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate via email + password. Returns user + token or error."""
        user = await self._find_by_email(email.strip().lower())
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

        pv = user.get("password_version", 0)
        token = self._create_token(user["id"], pv)
        return {"user": self._public_user(user), "token": token}

    async def login_google(self, id_token: str) -> dict[str, Any]:
        """Authenticate via Google ID token. Returns user + token or error."""
        token_info = await self._verify_google_token(id_token)
        if "error" in token_info:
            return token_info

        email = token_info["email"].lower()
        user = await self._find_by_email(email)

        if not user:
            return {"error": "No account found. Please register first."}

        if user.get("auth_method") == "email":
            return {
                "error": "This email is registered with an email account. Please sign in using your email and password.",
                "conflict": "email",
            }

        if user.get("status") == "pending_approval":
            await self._refresh_google_profile(
                user,
                given_name=token_info.get("given_name", ""),
                family_name=token_info.get("family_name", ""),
                google_id=token_info.get("sub", ""),
                profile_picture=token_info.get("picture", ""),
            )
            return {
                "user": self._public_user(user),
                "pending": True,
            }

        if user.get("status") == "rejected":
            return {"error": "Your account has been rejected. Please contact an administrator."}

        # Refresh profile fields from the fresh Google token before issuing session
        await self._refresh_google_profile(
            user,
            given_name=token_info.get("given_name", ""),
            family_name=token_info.get("family_name", ""),
            google_id=token_info.get("sub", ""),
            profile_picture=token_info.get("picture", ""),
        )
        pv = user.get("password_version", 0)
        token = self._create_token(user["id"], pv)
        return {"user": self._public_user(user), "token": token}

    # ── Session ────────────────────────────────────────────

    def _create_token(self, user_id: str, password_version: int = 0) -> str:
        """Create a signed session token embedding the password version."""
        return self._serializer.dumps({"uid": user_id, "pv": password_version})

    async def _refresh_google_profile(
        self,
        user: dict[str, Any],
        given_name: str,
        family_name: str,
        google_id: str,
        profile_picture: str,
    ) -> None:
        """Update a Google user's profile fields from a fresh Google token and persist.

        Only updates fields that Google provides — never touches ``dob``, ``role``,
        ``status``, or any other admin-managed field.
        Only overwrites stored values if the incoming value is non-empty,
        so manually-set fields (e.g. a custom profile picture) are preserved
        unless Google explicitly supplies a replacement.
        """
        changed = False
        if given_name and user.get("first_name") != given_name:
            user["first_name"] = given_name
            changed = True
        if family_name and user.get("last_name") != family_name:
            user["last_name"] = family_name
            changed = True
        if google_id and user.get("google_id") != google_id:
            user["google_id"] = google_id
            changed = True
        if profile_picture and user.get("profile_picture") != profile_picture:
            user["profile_picture"] = profile_picture
            changed = True
        if changed:
            await self._save(user)
            logger.debug(f"Refreshed Google profile for user {user['id']}")

    async def verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify a session token and return the user, or None."""
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
            user = await self._load(data["uid"])
            if not user or user.get("status") != "approved":
                return None
            # Invalidate token if password has been changed since it was issued
            if user.get("password_version", 0) != data.get("pv", 0):
                return None
            return self._public_user(user)
        except (BadSignature, SignatureExpired, KeyError):
            return None

    # ── User Self-Service ──────────────────────────────────

    async def update_profile(
        self,
        user_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        dob: str | None = None,
        profile_picture: str | None = None,
    ) -> dict[str, Any]:
        """Update a user's own profile fields. Returns updated public user or error."""
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if first_name is not None:
            user["first_name"] = first_name.strip()
        if last_name is not None:
            user["last_name"] = last_name.strip()
        if dob is not None:
            user["dob"] = dob
        if profile_picture is not None:
            user["profile_picture"] = profile_picture  # base64 PNG data URL

        await self._save(user)
        logger.info(f"User {user_id} updated their profile")
        return {"user": self._public_user(user)}

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> dict[str, Any]:
        """Change a user's password and bump the password_version to invalidate sessions."""
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if user.get("auth_method") == "google":
            return {"error": "Google-authenticated accounts cannot set a password here."}

        if not _bcrypt.checkpw(
            current_password.encode("utf-8"),
            user.get("password_hash", "").encode("utf-8"),
        ):
            return {"error": "Current password is incorrect."}

        # Hash new password
        new_hash = _bcrypt.hashpw(new_password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
        user["password_hash"] = new_hash
        # Increment password_version to invalidate all existing session tokens
        user["password_version"] = user.get("password_version", 0) + 1
        await self._save(user)

        logger.info(f"User {user_id} changed their password (version={user['password_version']})")
        return {"message": "Password changed successfully. Please log in again."}

    async def delete_account(
        self,
        user_id: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Permanently delete a user account.

        For email accounts, ``password`` must be provided and verified.
        For Google accounts, no password is required (they are verified via
        Google token on login — we trust the active session).
        """
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if user.get("auth_method") == "email":
            if not password:
                return {"error": "Please enter your password to confirm account deletion."}
            if not _bcrypt.checkpw(
                password.encode("utf-8"),
                user.get("password_hash", "").encode("utf-8"),
            ):
                return {"error": "Incorrect password. Account was not deleted."}

        async with self.engine.begin() as conn:
            await conn.execute(
                delete(users_table).where(users_table.c.id == user_id)
            )

        logger.info(f"User {user_id} ({user.get('email', '')}) deleted their account")
        return {"message": "Account deleted successfully."}

    # ── Admin Actions ──────────────────────────────────────

    async def approve_user(self, user_id: str, role: str) -> dict[str, Any]:
        """Approve a user and assign a role."""
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if role not in VALID_ROLES:
            return {"error": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"}

        user["status"] = "approved"
        user["role"] = role
        user["approved_at"] = datetime.now(timezone.utc)
        await self._save(user)

        logger.info(f"User {user_id} ({user['email']}) approved with role '{role}'")
        return {"user": self._public_user(user), "message": f"User approved as {role}"}

    async def reject_user(self, user_id: str) -> dict[str, Any]:
        """Reject a pending user."""
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        user["status"] = "rejected"
        await self._save(user)

        logger.info(f"User {user_id} ({user['email']}) rejected")
        return {"message": "User rejected"}

    async def update_role(self, user_id: str, new_role: str) -> dict[str, Any]:
        """Change an approved user's role."""
        user = await self._load(user_id)
        if not user:
            return {"error": "User not found"}

        if new_role not in VALID_ROLES:
            return {"error": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"}

        old_role = user.get("role")
        user["role"] = new_role
        await self._save(user)

        logger.info(f"User {user_id} role changed: {old_role} → {new_role}")
        return {
            "user": self._public_user(user),
            "message": f"Role changed from {old_role} to {new_role}",
        }

    async def list_users(self) -> list[dict[str, Any]]:
        """List all registered users."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(users_table).order_by(users_table.c.created_at.desc())
            )
            rows = result.mappings().all()
        return [self._public_user(dict(row)) for row in rows]

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Get a single user by ID (public view)."""
        user = await self._load(user_id)
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
                "picture": info.get("picture", ""),
            }
        except Exception as e:
            logger.error(f"Google token verification failed: {e}")
            return {"error": "Failed to verify Google token."}

    # ── Helpers ────────────────────────────────────────────

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        """Return a user dict with sensitive fields stripped."""
        created_at = user.get("created_at", "")
        approved_at = user.get("approved_at")
        # Convert datetime objects to ISO strings for JSON serialization
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        if hasattr(approved_at, "isoformat"):
            approved_at = approved_at.isoformat()
        return {
            "id": user["id"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "dob": user.get("dob", ""),
            "email": user.get("email", ""),
            "auth_method": user.get("auth_method", "email"),
            "status": user.get("status", "pending_approval"),
            "role": user.get("role"),
            "created_at": created_at,
            "approved_at": approved_at,
            "profile_picture": user.get("profile_picture"),
        }
