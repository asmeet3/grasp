"""Contribution Manager — handles user-submitted content requests.

Users can submit documents, code snippets, or plain text to be added
to the knowledge repository. Admins review, edit, and approve/reject
from the admin dashboard. Approved contributions are classified and
written into the Git-backed repo.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connectors.base import Document
from .repo.manager import RepoManager

logger = logging.getLogger(__name__)

VALID_CONTENT_TYPES = ("document", "code", "plain_text")
VALID_STATUSES = ("pending", "approved", "rejected")


class ContributionManager:
    """Manages user contribution requests stored as JSON files."""

    def __init__(self, state_dir: Path, repo_manager: RepoManager):
        self.contributions_dir = state_dir / "contributions"
        self.contributions_dir.mkdir(parents=True, exist_ok=True)
        self.repo_manager = repo_manager

    # ── Submit ─────────────────────────────────────────────

    def submit(
        self,
        title: str,
        content: str,
        content_type: str = "document",
        submitted_by: str = "",
        original_filename: str = "",
        original_file_ext: str = "",
    ) -> dict[str, Any]:
        """Create a new pending contribution request."""
        if content_type not in VALID_CONTENT_TYPES:
            content_type = "document"

        contribution_id = str(uuid.uuid4())[:12]
        contribution = {
            "id": contribution_id,
            "title": title.strip(),
            "content": content,
            "content_type": content_type,
            "submitted_by": submitted_by.strip(),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "admin_notes": "",
            "resolved_at": None,
            "original_filename": original_filename,
            "original_file_ext": original_file_ext,
        }

        filepath = self.contributions_dir / f"{contribution_id}.json"
        filepath.write_text(json.dumps(contribution, indent=2), encoding="utf-8")
        logger.info(f"New contribution submitted: '{title}' by {contribution['submitted_by']}")

        return contribution

    # ── Read ───────────────────────────────────────────────

    def _load(self, contribution_id: str) -> dict[str, Any] | None:
        """Load a single contribution by ID."""
        filepath = self.contributions_dir / f"{contribution_id}.json"
        if filepath.exists():
            return json.loads(filepath.read_text(encoding="utf-8"))
        return None

    def _save(self, contribution: dict[str, Any]):
        """Persist an updated contribution."""
        filepath = self.contributions_dir / f"{contribution['id']}.json"
        filepath.write_text(json.dumps(contribution, indent=2), encoding="utf-8")

    def get(self, contribution_id: str) -> dict[str, Any] | None:
        """Get a single contribution by ID."""
        return self._load(contribution_id)

    def list_all(self, status_filter: str | None = None) -> list[dict[str, Any]]:
        """List contributions, optionally filtered by status."""
        contributions = []
        for filepath in self.contributions_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                if status_filter is None or data.get("status") == status_filter:
                    contributions.append(data)
            except Exception as e:
                logger.warning(f"Failed to load contribution {filepath.name}: {e}")

        # Sort by submitted_at descending (newest first)
        contributions.sort(key=lambda c: c.get("submitted_at", ""), reverse=True)
        return contributions

    def list_pending(self) -> list[dict[str, Any]]:
        """List all pending contributions."""
        return self.list_all(status_filter="pending")

    def count_pending(self) -> int:
        """Count pending contributions."""
        return len(self.list_pending())

    # ── Admin Actions ──────────────────────────────────────

    def update_content(
        self,
        contribution_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any] | None:
        """Admin edits the contribution content before approval."""
        contribution = self._load(contribution_id)
        if not contribution:
            return None

        if contribution["status"] != "pending":
            return None

        if title is not None:
            contribution["title"] = title.strip()
        if content is not None:
            contribution["content"] = content

        self._save(contribution)
        logger.info(f"Contribution {contribution_id} updated by admin")
        return contribution

    async def approve(
        self,
        contribution_id: str,
        admin_notes: str = "",
    ) -> dict[str, Any]:
        """Approve a contribution — classify and write to the repo."""
        contribution = self._load(contribution_id)
        if not contribution:
            return {"error": "Contribution not found"}

        if contribution["status"] != "pending":
            return {"error": f"Contribution is already {contribution['status']}"}

        try:
            # Build a Document for the repo manager
            doc = Document(
                id=f"contribution-{contribution['id']}",
                source="user_contribution",
                title=contribution["title"],
                content=contribution["content"],
                url="",
            )

            # Classify and write to the repo
            info_type = await self.repo_manager.classify_and_write(doc)

            # Stage the pending changes
            self.repo_manager.stage_pending()

            # Update contribution status
            contribution["status"] = "approved"
            contribution["admin_notes"] = admin_notes
            contribution["resolved_at"] = datetime.now(timezone.utc).isoformat()
            contribution["classified_as"] = info_type
            self._save(contribution)

            logger.info(
                f"Contribution {contribution_id} approved → "
                f"classified as '{info_type}'"
            )

            return {
                "status": "approved",
                "message": f"Contribution approved and classified as '{info_type}'. "
                           f"It is now staged as a pending change.",
                "info_type": info_type,
            }
        except Exception as e:
            logger.error(f"Failed to approve contribution {contribution_id}: {e}")
            return {"error": str(e)}

    def reject(
        self,
        contribution_id: str,
        admin_notes: str = "",
    ) -> dict[str, Any]:
        """Reject a contribution."""
        contribution = self._load(contribution_id)
        if not contribution:
            return {"error": "Contribution not found"}

        if contribution["status"] != "pending":
            return {"error": f"Contribution is already {contribution['status']}"}

        contribution["status"] = "rejected"
        contribution["admin_notes"] = admin_notes
        contribution["resolved_at"] = datetime.now(timezone.utc).isoformat()
        self._save(contribution)

        logger.info(f"Contribution {contribution_id} rejected")
        return {"status": "rejected", "message": "Contribution rejected"}
