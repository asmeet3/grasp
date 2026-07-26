"""Immutable knowledge proposals and commit-index-activate saga."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .audit import PostgresAuditStore
from .connectors.base import Document
from .core.changes import require_transition
from .core.security import AuthContext
from .database import (
    active_revisions_table,
    index_jobs_table,
    knowledge_changesets_table,
)
from .index.vector_store import VectorStore
from .ingestion import IngestionCandidate
from .repo.manager import RepoManager

logger = logging.getLogger(__name__)

OPEN_STATES = ("draft", "awaiting_review", "approved", "applying", "failed")


class ChangeSetService:
    """One governed write path for every source of company knowledge."""

    def __init__(
        self,
        engine: AsyncEngine,
        repository: RepoManager,
        search_index: VectorStore,
        state_dir: Path,
        audit_store: PostgresAuditStore | None = None,
    ):
        self.engine = engine
        self.repository = repository
        self.search_index = search_index
        self.root = state_dir / "changesets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit = audit_store or PostgresAuditStore(engine)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def create(
        self,
        change_type: str,
        *,
        organization_id: str,
        creator_user_id: str | None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        change_set_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        values = {
            "id": change_set_id,
            "change_type": change_type,
            "organization_id": organization_id,
            "creator_user_id": creator_user_id,
            "base_commit_sha": self.repository.current_commit(),
            "state": "draft",
            "operations": [],
            "provenance": provenance or {},
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(knowledge_changesets_table.insert().values(**values))
        (self.root / change_set_id / "files").mkdir(parents=True, exist_ok=True)
        await self.audit.record(
            "changeset.created",
            actor_id=creator_user_id,
            organization_id=organization_id,
            resource_type="knowledge_changeset",
            resource_id=change_set_id,
            details={"change_type": change_type, "base_commit_sha": values["base_commit_sha"]},
        )
        return self._serialize(values)

    async def get(self, change_set_id: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(knowledge_changesets_table).where(
                    knowledge_changesets_table.c.id == change_set_id
                )
            )
            row = result.mappings().first()
        return self._serialize(dict(row)) if row else None

    async def list_pending(self, organization_id: str) -> list[dict[str, Any]]:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(knowledge_changesets_table)
                .where(
                    knowledge_changesets_table.c.organization_id == organization_id,
                    knowledge_changesets_table.c.state.in_(OPEN_STATES),
                )
                .order_by(knowledge_changesets_table.c.created_at.asc())
            )
            rows = result.mappings().all()
        return [self._serialize(dict(row)) for row in rows]

    async def stage_candidate(
        self,
        change_set_id: str,
        candidate: IngestionCandidate,
        *,
        info_type: str | None = None,
    ) -> str:
        """Normalize, validate, classify, and stage one immutable candidate."""
        self._validate_candidate(candidate)
        doc = candidate.to_document()
        if info_type is None:
            info_type = await self.repository.classify_document(doc)
        if candidate.deleted:
            repo_path = str(candidate.metadata.get("repo_path") or "")
            if not repo_path:
                raise ValueError("A tombstone must identify metadata.repo_path")
            operations = [
                {
                    "op": "delete",
                    "path": repo_path,
                    "document": {
                        "id": candidate.external_id,
                        "deleted": True,
                        "info_type": info_type,
                        "content_hash": candidate.content_hash,
                    },
                }
            ]
        else:
            operations = await asyncio.to_thread(self.repository.plan_document, doc, info_type)

        async with self._locks[change_set_id]:
            row = await self.get(change_set_id)
            if not row or row["state"] != "draft":
                raise RuntimeError("Change set is not open for staging")
            existing = row.get("operations") or []
            knowledge_operation = next(
                (operation for operation in operations if operation.get("document")),
                None,
            )
            if knowledge_operation:
                current_metadata = await asyncio.to_thread(
                    self.repository.get_committed_file_metadata,
                    knowledge_operation["path"],
                )
                if (
                    current_metadata.get("id") == candidate.external_id
                    and current_metadata.get("content_hash") == candidate.content_hash
                    and current_metadata.get("source_revision", "") == candidate.source_revision
                ):
                    return info_type
            operations = self._resolve_operation_collisions(existing, operations)
            combined = self._merge_operations(existing, operations)
            await self._update(
                change_set_id,
                operations=combined,
                updated_at=datetime.now(UTC),
            )
            await asyncio.to_thread(self._persist_staging_files, change_set_id, combined)
        return info_type

    async def submit(self, change_set_id: str) -> dict[str, Any]:
        row = await self.get(change_set_id)
        if not row or row["state"] != "draft":
            raise RuntimeError("Only draft change sets can be submitted")
        if not row.get("operations"):
            raise ValueError("Cannot submit an empty change set")
        require_transition(row["state"], "awaiting_review")
        await self._update(
            change_set_id,
            state="awaiting_review",
            updated_at=datetime.now(UTC),
        )
        return (await self.get(change_set_id)) or {}

    async def close_empty(self, change_set_id: str) -> None:
        """Close a no-op draft so it never enters the reviewer queue."""
        row = await self.get(change_set_id)
        if not row or row["state"] != "draft" or row.get("operations"):
            raise RuntimeError("Only an empty draft can be closed")
        require_transition("draft", "rejected")
        await self._update(
            change_set_id,
            state="rejected",
            review_explanation="No content changes after hash deduplication",
            reviewed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def diff(self, change_set_id: str, file_path: str) -> str:
        row = await self.get(change_set_id)
        if not row:
            return ""
        operation = next(
            (op for op in row.get("operations", []) if op.get("path") == file_path),
            None,
        )
        if not operation:
            return ""
        before = self.repository.read_committed_file(file_path, row["base_commit_sha"])
        after = "" if operation.get("op") == "delete" else str(operation.get("content", ""))
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )

    async def reject(
        self,
        change_set_id: str,
        reviewer: AuthContext,
        explanation: str = "",
    ) -> dict[str, Any]:
        row = await self.get(change_set_id)
        if not row or row["state"] not in ("draft", "awaiting_review", "failed"):
            raise RuntimeError("Change set cannot be rejected from its current state")
        require_transition(row["state"], "rejected")
        now = datetime.now(UTC)
        await self._update(
            change_set_id,
            state="rejected",
            reviewer_user_id=reviewer.user_id,
            reviewed_at=now,
            review_explanation=explanation,
            updated_at=now,
        )
        await self.audit.record(
            "changeset.rejected",
            actor_id=reviewer.user_id,
            organization_id=reviewer.organization_id,
            resource_type="knowledge_changeset",
            resource_id=change_set_id,
            details={"explanation": explanation},
        )
        return (await self.get(change_set_id)) or {}

    async def approve_and_activate(
        self,
        change_set_id: str,
        reviewer: AuthContext,
        *,
        explanation: str = "",
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Run the idempotent Git -> outbox -> green index -> activate saga."""
        async with self._locks[change_set_id]:
            row = await self.get(change_set_id)
            if not row:
                raise ValueError("Change set not found")
            if row["organization_id"] != reviewer.organization_id:
                raise PermissionError("Cross-organization review is not allowed")
            if row["state"] == "active":
                return row
            if row["state"] not in ("awaiting_review", "failed"):
                raise RuntimeError(f"Change set cannot be approved from state {row['state']}")
            pending = await self.list_pending(reviewer.organization_id)
            blockers = [
                item
                for item in pending
                if item["id"] != change_set_id
                and item["state"] in ("applying", "failed")
                and item["created_at"] < row["created_at"]
            ]
            if blockers:
                raise RuntimeError(
                    f"Earlier interrupted change set must be resolved first: {blockers[0]['id']}"
                )

            now = datetime.now(UTC)
            commit_sha = row.get("final_commit_sha")
            if not commit_sha:
                current = self.repository.current_commit()
                if current != row["base_commit_sha"]:
                    conflicts = self._find_replay_conflicts(row, current)
                    if conflicts:
                        await self._update(
                            change_set_id,
                            error=json.dumps(
                                {
                                    "type": "base_commit_conflict",
                                    "current_commit": current,
                                    "files": conflicts,
                                }
                            ),
                            updated_at=now,
                        )
                        raise RuntimeError("base_commit_conflict: " + ", ".join(conflicts[:10]))
                    await self._update(
                        change_set_id,
                        base_commit_sha=current,
                        error="",
                        updated_at=now,
                    )
                    row["base_commit_sha"] = current
                await self._update(
                    change_set_id,
                    state="applying",
                    reviewer_user_id=reviewer.user_id,
                    reviewed_at=now,
                    review_explanation=explanation,
                    error="",
                    updated_at=now,
                )
                require_transition(row["state"], "applying")
                message = (
                    commit_message
                    or f"knowledge: activate {row['change_type']} {change_set_id[:8]}"
                )
                commit_sha = await asyncio.to_thread(
                    self.repository.apply_operations_and_commit,
                    row["operations"],
                    message,
                    row["base_commit_sha"],
                )
                job_id = str(uuid.uuid4())
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(knowledge_changesets_table)
                        .where(knowledge_changesets_table.c.id == change_set_id)
                        .values(final_commit_sha=commit_sha, state="applying", updated_at=now)
                    )
                    await conn.execute(
                        pg_insert(index_jobs_table)
                        .values(
                            id=job_id,
                            change_set_id=change_set_id,
                            commit_sha=commit_sha,
                            state="pending",
                        )
                        .on_conflict_do_nothing(index_elements=["change_set_id", "commit_sha"])
                    )
            elif row["state"] == "failed":
                require_transition("failed", "applying")
                await self._update(
                    change_set_id,
                    state="applying",
                    reviewer_user_id=reviewer.user_id,
                    reviewed_at=now,
                    review_explanation=explanation,
                    error="",
                    updated_at=now,
                )

            previous_index = self.search_index.active_index_name
            previous_commit = self.search_index.active_commit
            manifest: dict[str, Any] | None = None
            try:
                await asyncio.to_thread(self.repository.push_commit, commit_sha)
                manifest = await self._build_and_activate_index(row, commit_sha)
                index_name = manifest["index_name"]
                activated_at = datetime.now(UTC)
                require_transition("applying", "active")
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(index_jobs_table)
                        .where(
                            index_jobs_table.c.change_set_id == change_set_id,
                            index_jobs_table.c.commit_sha == commit_sha,
                        )
                        .values(
                            state="completed",
                            actual_manifest=manifest,
                            expected_manifest=manifest,
                            updated_at=activated_at,
                            error="",
                        )
                    )
                    await conn.execute(
                        pg_insert(active_revisions_table)
                        .values(
                            organization_id=row["organization_id"],
                            commit_sha=commit_sha,
                            index_name=index_name,
                            embedding_model=self.search_index.embedding_model,
                            index_schema_version=self.search_index.INDEX_SCHEMA_VERSION,
                            manifest=manifest,
                            activated_at=activated_at,
                        )
                        .on_conflict_do_update(
                            index_elements=["organization_id"],
                            set_={
                                "commit_sha": commit_sha,
                                "index_name": index_name,
                                "embedding_model": self.search_index.embedding_model,
                                "index_schema_version": self.search_index.INDEX_SCHEMA_VERSION,
                                "manifest": manifest,
                                "activated_at": activated_at,
                            },
                        )
                    )
                    await conn.execute(
                        # Activation is the only successful terminal transition.
                        update(knowledge_changesets_table)
                        .where(knowledge_changesets_table.c.id == change_set_id)
                        .values(state="active", error="", updated_at=activated_at)
                    )
                await self.audit.record(
                    "changeset.activated",
                    actor_id=reviewer.user_id,
                    organization_id=reviewer.organization_id,
                    resource_type="knowledge_changeset",
                    resource_id=change_set_id,
                    details={"commit_sha": commit_sha, "manifest": manifest},
                )
                return (await self.get(change_set_id)) or {}
            except Exception as exc:
                logger.exception("Activation failed for change set %s", change_set_id)
                if (
                    manifest
                    and self.search_index.active_index_name == manifest.get("index_name")
                    and previous_index != manifest.get("index_name")
                ):
                    await asyncio.to_thread(
                        self.search_index.activate_revision,
                        previous_index,
                        previous_commit,
                    )
                failed_at = datetime.now(UTC)
                require_transition("applying", "failed")
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(index_jobs_table)
                        .where(
                            index_jobs_table.c.change_set_id == change_set_id,
                            index_jobs_table.c.commit_sha == commit_sha,
                        )
                        .values(
                            state="failed",
                            attempts=index_jobs_table.c.attempts + 1,
                            error=str(exc),
                            updated_at=failed_at,
                        )
                    )
                    await conn.execute(
                        update(knowledge_changesets_table)
                        .where(knowledge_changesets_table.c.id == change_set_id)
                        .values(
                            state="failed",
                            error=str(exc),
                            retry_count=knowledge_changesets_table.c.retry_count + 1,
                            updated_at=failed_at,
                        )
                    )
                raise

    async def _build_and_activate_index(
        self, row: dict[str, Any], commit_sha: str
    ) -> dict[str, Any]:
        index_name = await asyncio.to_thread(self.search_index.begin_revision, commit_sha)
        document_chunks: dict[str, int] = {}
        try:
            documents: dict[str, dict] = {}
            for operation in row.get("operations") or []:
                payload = operation.get("document")
                if payload and payload.get("id"):
                    documents[str(payload["id"])] = payload
            for doc_id, payload in documents.items():
                if payload.get("deleted"):
                    await asyncio.to_thread(
                        self.search_index.delete_document,
                        doc_id,
                        index_name=index_name,
                    )
                    document_chunks[doc_id] = 0
                    continue
                document = Document(
                    id=doc_id,
                    source=str(payload.get("source") or "unknown"),
                    title=str(payload.get("title") or "Untitled"),
                    content=str(payload.get("content") or ""),
                    url=str(payload.get("url") or ""),
                    updated_at=datetime.fromisoformat(payload["updated_at"]),
                    metadata=dict(payload.get("metadata") or {}),
                )
                chunks = await asyncio.to_thread(
                    self.search_index.index_committed_document,
                    document,
                    str(payload.get("info_type") or "topics"),
                    commit_sha=commit_sha,
                    content_hash=str(payload["content_hash"]),
                    organization_id=str(row["organization_id"]),
                    index_name=index_name,
                )
                document_chunks[doc_id] = chunks
            total_chunks = await asyncio.to_thread(self.search_index.collection_count, index_name)
            manifest = {
                "index_name": index_name,
                "commit_sha": commit_sha,
                "document_chunks": document_chunks,
                "total_chunks": total_chunks,
                "embedding_model": self.search_index.embedding_model,
                "index_schema_version": self.search_index.INDEX_SCHEMA_VERSION,
            }
            await asyncio.to_thread(self.search_index.activate_revision, index_name, commit_sha)
            return manifest
        except Exception:
            await asyncio.to_thread(self.search_index.abort_revision, index_name)
            raise

    async def reconcile(self) -> dict[str, int]:
        """Make interrupted saga states explicit and safely retryable."""
        repaired = 0
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(knowledge_changesets_table).where(
                    knowledge_changesets_table.c.state == "applying"
                )
            )
            rows = result.mappings().all()
            for row in rows:
                if not row.get("final_commit_sha"):
                    await conn.execute(
                        update(knowledge_changesets_table)
                        .where(knowledge_changesets_table.c.id == row["id"])
                        .values(
                            state="failed",
                            error="Interrupted before commit metadata was recorded",
                            updated_at=datetime.now(UTC),
                        )
                    )
                    repaired += 1
            active_result = await conn.execute(select(active_revisions_table))
            active_rows = active_result.mappings().all()
        if active_rows:
            active = active_rows[0]
            if self.search_index.active_index_name != active[
                "index_name"
            ] and self.search_index.collection_exists(active["index_name"]):
                await asyncio.to_thread(
                    self.search_index.activate_revision,
                    active["index_name"],
                    active["commit_sha"],
                )
                repaired += 1
        elif (
            self.search_index.active_index_name != self.search_index.COLLECTION_NAME
            and self.search_index.collection_exists(self.search_index.COLLECTION_NAME)
        ):
            await asyncio.to_thread(
                self.search_index.activate_revision,
                self.search_index.COLLECTION_NAME,
                "legacy",
            )
            repaired += 1
        return {"repaired": repaired}

    async def rebuild_from_git(self, organization_id: str = "default") -> dict[str, Any]:
        """Blue/green rebuild of the complete derived index from committed Markdown."""
        commit_sha = self.repository.current_commit()
        previous_index = self.search_index.active_index_name
        previous_commit = self.search_index.active_commit
        index_name = await asyncio.to_thread(
            self.search_index.begin_revision, commit_sha, clone_active=False
        )
        document_chunks: dict[str, int] = {}
        try:
            knowledge_root = self.repository.repo_path / "knowledge"
            paths = sorted(knowledge_root.rglob("*.md")) if knowledge_root.exists() else []
            for path in paths:
                if path.name == "README.md":
                    continue
                relative = path.relative_to(self.repository.repo_path).as_posix()
                committed = self.repository.read_committed_file(relative, commit_sha)
                if not committed:
                    continue
                metadata = self.repository._parse_frontmatter(committed)
                body_end = committed.find("\n---", 4)
                body = committed[body_end + 4 :].strip() if body_end >= 0 else committed
                doc_id = str(metadata.get("id") or relative)
                acl = metadata.get("acl_principals") or [f"organization:{organization_id}"]
                updated_at = datetime.now(UTC)
                date_value = str(metadata.get("date") or "")
                if date_value:
                    try:
                        updated_at = datetime.fromisoformat(date_value).replace(tzinfo=UTC)
                    except ValueError:
                        pass
                content_hash = (
                    str(metadata.get("content_hash") or "")
                    or hashlib.sha256(body.encode("utf-8")).hexdigest()
                )
                document = Document(
                    id=doc_id,
                    source=str(metadata.get("source") or "committed_repository"),
                    title=str(metadata.get("title") or path.stem),
                    content=body,
                    updated_at=updated_at,
                    metadata={
                        "repo_path": relative,
                        "acl_principals": acl,
                        "domain": metadata.get("domain", "general"),
                        "sensitivity": metadata.get("sensitivity", "internal"),
                    },
                )
                document_chunks[doc_id] = await asyncio.to_thread(
                    self.search_index.index_committed_document,
                    document,
                    str(metadata.get("type") or "topics"),
                    commit_sha=commit_sha,
                    content_hash=content_hash,
                    organization_id=organization_id,
                    index_name=index_name,
                )
            manifest = {
                "index_name": index_name,
                "commit_sha": commit_sha,
                "document_chunks": document_chunks,
                "total_chunks": await asyncio.to_thread(
                    self.search_index.collection_count, index_name
                ),
                "embedding_model": self.search_index.embedding_model,
                "index_schema_version": self.search_index.INDEX_SCHEMA_VERSION,
                "full_rebuild": True,
            }
            await asyncio.to_thread(self.search_index.activate_revision, index_name, commit_sha)
            async with self.engine.begin() as conn:
                await conn.execute(
                    pg_insert(active_revisions_table)
                    .values(
                        organization_id=organization_id,
                        commit_sha=commit_sha,
                        index_name=index_name,
                        embedding_model=self.search_index.embedding_model,
                        index_schema_version=self.search_index.INDEX_SCHEMA_VERSION,
                        manifest=manifest,
                    )
                    .on_conflict_do_update(
                        index_elements=["organization_id"],
                        set_={
                            "commit_sha": commit_sha,
                            "index_name": index_name,
                            "embedding_model": self.search_index.embedding_model,
                            "index_schema_version": self.search_index.INDEX_SCHEMA_VERSION,
                            "manifest": manifest,
                            "activated_at": datetime.now(UTC),
                        },
                    )
                )
            return manifest
        except Exception:
            if self.search_index.active_index_name == index_name:
                await asyncio.to_thread(
                    self.search_index.activate_revision,
                    previous_index,
                    previous_commit,
                )
            await asyncio.to_thread(self.search_index.abort_revision, index_name)
            raise

    async def _update(self, change_set_id: str, **values: Any) -> None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(knowledge_changesets_table)
                .where(knowledge_changesets_table.c.id == change_set_id)
                .values(**values)
            )
            if result.rowcount != 1:
                raise ValueError("Change set not found")

    def _persist_staging_files(self, change_set_id: str, operations: list[dict]) -> None:
        files_root = (self.root / change_set_id / "files").resolve()
        for operation in operations:
            if operation.get("op") != "write":
                continue
            path = (files_root / str(operation["path"])).resolve()
            try:
                path.relative_to(files_root)
            except ValueError as exc:
                raise ValueError("Staged operation escaped its worktree") from exc
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(operation.get("content", "")), encoding="utf-8")
        manifest = [
            {key: value for key, value in operation.items() if key != "content"}
            for operation in operations
        ]
        (self.root / change_set_id / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )

    @staticmethod
    def _merge_operations(existing: list[dict], incoming: list[dict]) -> list[dict]:
        merged = {str(operation["path"]): operation for operation in existing}
        for operation in incoming:
            merged[str(operation["path"])] = operation
        return list(merged.values())

    @staticmethod
    def _resolve_operation_collisions(existing: list[dict], incoming: list[dict]) -> list[dict]:
        """Keep identical titles with different external IDs as separate files."""
        import hashlib

        occupied = {str(operation["path"]): operation.get("document_id") for operation in existing}
        resolved: list[dict] = []
        for incoming_operation in incoming:
            operation = dict(incoming_operation)
            path = str(operation["path"])
            document_id = operation.get("document_id")
            if path in occupied and occupied[path] != document_id:
                original = Path(path)
                suffix = hashlib.sha256(str(document_id).encode("utf-8")).hexdigest()[:8]
                operation["path"] = str(
                    original.with_name(f"{original.stem}--{suffix}{original.suffix}")
                ).replace("\\", "/")
                if operation.get("document"):
                    payload = dict(operation["document"])
                    metadata = dict(payload.get("metadata") or {})
                    metadata["repo_path"] = operation["path"]
                    payload["metadata"] = metadata
                    operation["document"] = payload
            occupied[str(operation["path"])] = document_id
            resolved.append(operation)
        return resolved

    def _find_replay_conflicts(self, row: dict[str, Any], current_commit: str) -> list[str]:
        """Detect text conflicts while allowing replay over unrelated commits."""
        conflicts: list[str] = []
        for operation in row.get("operations") or []:
            path = str(operation.get("path") or "")
            base_content = self.repository.read_committed_file(path, row["base_commit_sha"])
            current_content = self.repository.read_committed_file(path, current_commit)
            proposed_content = (
                "" if operation.get("op") == "delete" else str(operation.get("content", ""))
            )
            if base_content != current_content and proposed_content != current_content:
                conflicts.append(path)
        return conflicts

    @staticmethod
    def _validate_candidate(candidate: IngestionCandidate) -> None:
        if not candidate.external_id or not candidate.source:
            raise ValueError("Candidate requires stable source and external IDs")
        if not candidate.deleted and (not candidate.title.strip() or not candidate.content.strip()):
            raise ValueError("Candidate requires non-empty title and content")
        if not candidate.acl_principals:
            raise ValueError("Candidate requires explicit ACL principals")

    @staticmethod
    def _serialize(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for key, value in result.items():
            if hasattr(value, "isoformat"):
                result[key] = value.isoformat()
        return result
