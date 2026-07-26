"""Contribution Manager — handles user-submitted content requests.

Users can submit documents, code snippets, or plain text to be added
to the knowledge repository. Admins review, edit, and approve/reject
from the admin dashboard. Approved contributions are classified and
written into the Git-backed repo.

Contributions are stored in PostgreSQL. Uploaded original files remain
on disk under the configured state directory.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from .changesets import ChangeSetService
from .connectors.base import Document
from .core.security import AuthContext
from .database import contributions_table
from .ingestion import IngestionCandidate
from .repo.manager import RepoManager

logger = logging.getLogger(__name__)

VALID_CONTENT_TYPES = ("document", "code", "plain_text")


class ContributionManager:
    """Manages user contribution requests stored in PostgreSQL."""

    def __init__(
        self,
        engine: AsyncEngine,
        repo_manager: RepoManager,
        change_sets: ChangeSetService,
        state_dir: Path,
    ):
        self.engine = engine
        self.repo_manager = repo_manager
        self.change_sets = change_sets
        self.contributions_dir = state_dir / "contributions"
        self.contributions_dir.mkdir(parents=True, exist_ok=True)

    # Submission

    async def submit(
        self,
        title: str,
        content: str,
        content_type: str = "document",
        submitted_by: str = "",
        submitter_user_id: str = "",
        organization_id: str = "default",
        original_filename: str = "",
        original_file_ext: str = "",
    ) -> dict[str, Any]:
        """Create a new pending contribution request."""
        if content_type not in VALID_CONTENT_TYPES:
            content_type = "document"

        contribution_id = str(uuid.uuid4())[:12]
        contribution = {
            "id": contribution_id,
            "organization_id": organization_id,
            "title": title.strip(),
            "content": content,
            "content_type": content_type,
            "submitted_by": submitted_by.strip(),
            "submitter_user_id": submitter_user_id,
            "submitted_at": datetime.now(UTC),
            "status": "pending",
            "admin_notes": "",
            "resolved_at": None,
            "original_filename": original_filename,
            "original_file_ext": original_file_ext,
        }

        async with self.engine.begin() as conn:
            await conn.execute(contributions_table.insert().values(**contribution))

        logger.info(f"New contribution submitted: '{title}' by {contribution['submitted_by']}")
        return self._serialize(contribution)

    # Queries

    async def _load(self, contribution_id: str) -> dict[str, Any] | None:
        """Load a single contribution by ID."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(contributions_table).where(contributions_table.c.id == contribution_id)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def get(self, contribution_id: str) -> dict[str, Any] | None:
        """Get a single contribution by ID."""
        row = await self._load(contribution_id)
        return self._serialize(row) if row else None

    async def list_all(self, status_filter: str | None = None) -> list[dict[str, Any]]:
        """List contributions, optionally filtered by status."""
        async with self.engine.begin() as conn:
            stmt = select(contributions_table)
            if status_filter is not None:
                stmt = stmt.where(contributions_table.c.status == status_filter)
            stmt = stmt.order_by(contributions_table.c.submitted_at.desc())
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._serialize(dict(row)) for row in rows]

    async def list_pending(self) -> list[dict[str, Any]]:
        """List all pending contributions."""
        return await self.list_all(status_filter="pending")

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(contributions_table)
                .where(contributions_table.c.submitter_user_id == user_id)
                .order_by(contributions_table.c.submitted_at.desc())
            )
            rows = result.mappings().all()
        return [self._serialize(dict(row)) for row in rows]

    async def count_pending(self) -> int:
        """Count pending contributions."""
        pending = await self.list_pending()
        return len(pending)

    # Review actions

    async def update_content(
        self,
        contribution_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any] | None:
        """Admin edits the contribution content before approval."""
        contribution = await self._load(contribution_id)
        if not contribution:
            return None

        if contribution["status"] != "pending":
            return None

        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title.strip()
        if content is not None:
            updates["content"] = content

        if updates:
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(contributions_table)
                    .where(contributions_table.c.id == contribution_id)
                    .values(**updates)
                )
            contribution.update(updates)

        logger.info(f"Contribution {contribution_id} updated by admin")
        return self._serialize(contribution)

    async def approve(
        self,
        contribution_id: str,
        reviewer: AuthContext,
        admin_notes: str = "",
    ) -> dict[str, Any]:
        """Approve a contribution — classify and write to the repo."""
        contribution = await self._load(contribution_id)
        if not contribution:
            return {"error": "Contribution not found"}

        if contribution["status"] != "pending":
            return {"error": f"Contribution is already {contribution['status']}"}

        try:
            if contribution["organization_id"] != reviewer.organization_id:
                return {"error": "Cross-organization review is not allowed"}

            doc = Document(
                id=f"contribution-{contribution['id']}",
                source="user_contribution",
                title=contribution["title"],
                content=contribution["content"],
                url="",
                metadata={
                    "organization_id": contribution["organization_id"],
                    "acl_principals": [f"organization:{contribution['organization_id']}"],
                    "domain": "general",
                    "sensitivity": "internal",
                },
            )
            change_set = None
            info_type = contribution.get("classified_as")
            if contribution.get("change_set_id"):
                change_set = await self.change_sets.get(contribution["change_set_id"])
            if not change_set:
                change_set = await self.change_sets.create(
                    "contribution",
                    organization_id=contribution["organization_id"],
                    creator_user_id=contribution["submitter_user_id"],
                    provenance={"contribution_id": contribution_id},
                )
                candidate = IngestionCandidate.from_document(
                    doc, organization_id=contribution["organization_id"]
                )
                info_type = await self.change_sets.stage_candidate(change_set["id"], candidate)
                await self.change_sets.submit(change_set["id"])
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(contributions_table)
                        .where(contributions_table.c.id == contribution_id)
                        .values(change_set_id=change_set["id"], classified_as=info_type)
                    )
            await self.change_sets.approve_and_activate(
                change_set["id"],
                reviewer,
                explanation=admin_notes,
                commit_message=f"contribution: {contribution['title']}",
            )

            now = datetime.now(UTC)
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(contributions_table)
                    .where(contributions_table.c.id == contribution_id)
                    .values(
                        status="approved",
                        admin_notes=admin_notes,
                        reviewer_user_id=reviewer.user_id,
                        resolved_at=now,
                        classified_as=info_type,
                        change_set_id=change_set["id"],
                    )
                )

            logger.info(f"Contribution {contribution_id} approved → classified as '{info_type}'")

            return {
                "status": "approved",
                "message": f"Contribution approved, committed, indexed, and activated as '{info_type}'.",
                "info_type": info_type or "topics",
            }
        except Exception as e:
            logger.error(f"Failed to approve contribution {contribution_id}: {e}")
            return {"error": str(e)}

    async def reject(
        self,
        contribution_id: str,
        admin_notes: str = "",
    ) -> dict[str, Any]:
        """Reject a contribution."""
        contribution = await self._load(contribution_id)
        if not contribution:
            return {"error": "Contribution not found"}

        if contribution["status"] != "pending":
            return {"error": f"Contribution is already {contribution['status']}"}

        now = datetime.now(UTC)
        async with self.engine.begin() as conn:
            await conn.execute(
                update(contributions_table)
                .where(contributions_table.c.id == contribution_id)
                .values(
                    status="rejected",
                    admin_notes=admin_notes,
                    resolved_at=now,
                )
            )

        logger.info(f"Contribution {contribution_id} rejected")
        return {"status": "rejected", "message": "Contribution rejected"}

    # Serialization

    @staticmethod
    def _serialize(contribution: dict[str, Any]) -> dict[str, Any]:
        """Ensure datetime fields are ISO-formatted strings for JSON."""
        result = dict(contribution)
        for key in ("submitted_at", "resolved_at"):
            val = result.get(key)
            if hasattr(val, "isoformat"):
                result[key] = val.isoformat()
        return result
