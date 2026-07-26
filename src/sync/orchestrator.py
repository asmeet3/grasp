"""Sync orchestrator — coordinates parallel retrieval across all connectors.

Handles full sync (checkpointed), incremental sync, parallel worker
execution, and pending changeset generation for human approval.

Sync state (last sync, sync log) is persisted in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..changesets import ChangeSetService
from ..connectors.base import BaseConnector, Document
from ..database import sync_state_table
from ..ingestion import IngestionCandidate
from ..repo.manager import RepoManager
from .checkpoints import CheckpointManager

logger = logging.getLogger(__name__)


class WorkerStatus:
    """Tracks the status of an individual connector worker."""

    def __init__(self, connector_name: str):
        self.connector_name = connector_name
        self.status: str = "pending"  # pending | running | completed | failed
        self.docs_fetched: int = 0
        self.errors: list[str] = []
        self.started_at: float | None = None
        self.completed_at: float | None = None

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or time.time()
        return end - self.started_at

    def to_dict(self) -> dict:
        return {
            "connector": self.connector_name,
            "status": self.status,
            "docs_fetched": self.docs_fetched,
            "errors": self.errors,
            "elapsed_seconds": round(self.elapsed, 2),
        }


class SyncOrchestrator:
    """Orchestrates parallel retrieval from all configured connectors."""

    def __init__(
        self,
        connectors: dict[str, BaseConnector],
        repo_manager: RepoManager,
        change_sets: ChangeSetService,
        checkpoints: CheckpointManager,
        engine: AsyncEngine,
        organization_id: str = "default",
        overlap_seconds: int = 300,
    ):
        self.connectors = connectors
        self.repo_manager = repo_manager
        self.change_sets = change_sets
        self.checkpoints = checkpoints
        self.engine = engine
        self.organization_id = organization_id
        self.overlap_seconds = overlap_seconds

        self._sync_running = False
        self._worker_statuses: dict[str, WorkerStatus] = {}
        self._active_change_set_id: str | None = None

    # Public interface

    @property
    def is_running(self) -> bool:
        return self._sync_running

    @property
    def worker_statuses(self) -> dict[str, dict]:
        return {name: ws.to_dict() for name, ws in self._worker_statuses.items()}

    async def get_last_sync(self) -> dict | None:
        """Read the last sync state from the database."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(sync_state_table).order_by(sync_state_table.c.timestamp.desc()).limit(1)
            )
            row = result.mappings().first()
        if row:
            return self._row_to_sync_dict(row)
        return None

    async def needs_full_sync(self) -> bool:
        """Check if a full (initial) sync is required."""
        return (await self.get_last_sync()) is None

    async def run_sync(self) -> dict:
        """Run a sync — full or incremental depending on state.

        If the last sync had any failed connectors, those connectors
        get a full sync while successful ones get incremental.
        """
        if self._sync_running:
            return {"error": "Sync already in progress"}

        lock_connection = await self.engine.connect()
        lock_acquired = bool(
            (
                await lock_connection.execute(
                    text("SELECT pg_try_advisory_lock(hashtext('grasp:sync'))")
                )
            ).scalar()
        )
        if not lock_acquired:
            await lock_connection.close()
            return {"error": "Sync already running on another replica"}

        self._sync_running = True
        self._worker_statuses = {}
        sync_started_at = datetime.now(UTC)
        try:
            change_set = await self.change_sets.create(
                "sync",
                organization_id=self.organization_id,
                creator_user_id=None,
                provenance={"started_at": sync_started_at.isoformat()},
            )
        except Exception:
            self._sync_running = False
            await lock_connection.execute(text("SELECT pg_advisory_unlock(hashtext('grasp:sync'))"))
            await lock_connection.close()
            raise
        self._active_change_set_id = change_set["id"]

        try:
            if await self.needs_full_sync():
                logger.info("Starting FULL sync (no previous sync found)")
                result = await self._full_sync()
            else:
                last_sync = await self.get_last_sync()
                since_str = last_sync.get("watermark") or last_sync["timestamp"]
                since = (
                    datetime.fromisoformat(since_str) if isinstance(since_str, str) else since_str
                )
                since = since - timedelta(seconds=self.overlap_seconds)

                # Check which connectors failed last time
                last_workers = last_sync.get("workers", {})
                failed_connectors = {
                    name for name, info in last_workers.items() if info.get("status") == "failed"
                }

                if failed_connectors:
                    logger.info(
                        f"Starting MIXED sync — full for {failed_connectors}, "
                        f"incremental for others (since {since_str})"
                    )
                    result = await self._mixed_sync(since, failed_connectors)
                else:
                    logger.info(f"Starting INCREMENTAL sync (since {since_str})")
                    result = await self._incremental_sync(since)

            result["started_at"] = sync_started_at.isoformat()
            result["watermark"] = sync_started_at.isoformat()
            result["change_set_id"] = self._active_change_set_id

            staged = await self.change_sets.get(self._active_change_set_id)
            if staged and staged.get("operations"):
                await self.change_sets.submit(self._active_change_set_id)
            else:
                await self.change_sets.close_empty(self._active_change_set_id)

            # Save sync state
            await self._save_sync_state(result)

            return result
        except Exception as e:
            logger.error(f"Sync failed: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}
        finally:
            self._sync_running = False
            self._active_change_set_id = None
            await lock_connection.execute(text("SELECT pg_advisory_unlock(hashtext('grasp:sync'))"))
            await lock_connection.close()

    # Full sync

    async def _full_sync(self) -> dict:
        """Run a full sync with all connectors in parallel."""
        tasks = []
        for name, connector in self.connectors.items():
            checkpoint = await self.checkpoints.load_checkpoint(name)
            ws = WorkerStatus(name)
            self._worker_statuses[name] = ws
            tasks.append(self._run_full_worker(connector, ws, checkpoint))

        # Run all workers in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        total_docs = 0
        worker_results = {}
        for name, result in zip(self.connectors.keys(), results, strict=True):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}
                await self.checkpoints.clear_checkpoint(name)

        return {
            "type": "full",
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def _mixed_sync(self, since: datetime, full_sync_connectors: set[str]) -> dict:
        """Run full sync for failed connectors and incremental for the rest."""
        tasks = []
        for name, connector in self.connectors.items():
            ws = WorkerStatus(name)
            self._worker_statuses[name] = ws

            if name in full_sync_connectors:
                checkpoint = await self.checkpoints.load_checkpoint(name)
                tasks.append(self._run_full_worker(connector, ws, checkpoint))
            else:
                tasks.append(self._run_incremental_worker(connector, ws, since))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_docs = 0
        worker_results = {}
        for name, result in zip(self.connectors.keys(), results, strict=True):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}
                if name in full_sync_connectors:
                    await self.checkpoints.clear_checkpoint(name)

        return {
            "type": "mixed",
            "since": since.isoformat(),
            "full_connectors": list(full_sync_connectors),
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def _run_full_worker(
        self, connector: BaseConnector, ws: WorkerStatus, checkpoint: dict | None
    ):
        """Worker coroutine for full retrieval of a single connector."""
        ws.status = "running"
        ws.started_at = time.time()

        try:
            async for batch in connector.full_retrieve(checkpoint):
                for doc in batch:
                    await self._process_document(doc)
                    ws.docs_fetched += 1

                # Save checkpoint after each batch
                state = connector.get_checkpoint_state()
                await self.checkpoints.save_checkpoint(connector.name, state)

            ws.status = "completed"
        except Exception as e:
            ws.status = "failed"
            ws.errors.append(f"{type(e).__name__}: {e}")
            logger.error(f"Worker {connector.name} failed: {e}\n{traceback.format_exc()}")
            raise
        finally:
            ws.completed_at = time.time()

    # Incremental sync

    async def _incremental_sync(self, since: datetime) -> dict:
        """Run an incremental sync with all connectors in parallel."""
        tasks = []
        for name, connector in self.connectors.items():
            ws = WorkerStatus(name)
            self._worker_statuses[name] = ws
            tasks.append(self._run_incremental_worker(connector, ws, since))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_docs = 0
        worker_results = {}
        for name, result in zip(self.connectors.keys(), results, strict=True):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}

        return {
            "type": "incremental",
            "since": since.isoformat(),
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def _run_incremental_worker(
        self, connector: BaseConnector, ws: WorkerStatus, since: datetime
    ):
        """Worker coroutine for incremental retrieval."""
        ws.status = "running"
        ws.started_at = time.time()

        try:
            async for batch in connector.incremental_retrieve(since):
                for doc in batch:
                    await self._process_document(doc)
                    ws.docs_fetched += 1

            ws.status = "completed"
        except Exception as e:
            ws.status = "failed"
            ws.errors.append(f"{type(e).__name__}: {e}")
            logger.error(f"Worker {connector.name} failed: {e}\n{traceback.format_exc()}")
            raise
        finally:
            ws.completed_at = time.time()

    # Document processing

    async def _process_document(self, doc: Document):
        """Normalize and stage a document; committed indexes remain untouched."""
        try:
            if not self._active_change_set_id:
                raise RuntimeError("No active sync change set")
            candidate = IngestionCandidate.from_document(doc, organization_id=self.organization_id)
            await self.change_sets.stage_candidate(self._active_change_set_id, candidate)

        except Exception as e:
            logger.error(f"Failed to process document {doc.id}: {e}")
            raise

    # State management

    async def _save_sync_state(self, result: dict):
        """Save the sync result to the database."""
        # Extract fields for the table columns
        sync_type = result.get("type", "unknown")
        timestamp_str = result.get("timestamp", datetime.now(UTC).isoformat())
        timestamp = (
            datetime.fromisoformat(timestamp_str)
            if isinstance(timestamp_str, str)
            else timestamp_str
        )
        total_docs = result.get("total_docs", 0)
        workers = result.get("workers", {})
        started_at_value = result.get("started_at", timestamp)
        started_at = (
            datetime.fromisoformat(started_at_value)
            if isinstance(started_at_value, str)
            else started_at_value
        )
        watermark_value = result.get("watermark", started_at)
        watermark = (
            datetime.fromisoformat(watermark_value)
            if isinstance(watermark_value, str)
            else watermark_value
        )
        # Store additional fields (since, full_connectors, etc.) in details
        details = {
            k: v
            for k, v in result.items()
            if k not in ("type", "timestamp", "total_docs", "workers")
        }

        async with self.engine.begin() as conn:
            await conn.execute(
                sync_state_table.insert().values(
                    sync_type=sync_type,
                    timestamp=timestamp,
                    started_at=started_at,
                    watermark=watermark,
                    total_docs=total_docs,
                    workers=workers,
                    details=details,
                    change_set_id=result.get("change_set_id"),
                )
            )

        # Prune old entries: keep only last 100
        async with self.engine.begin() as conn:
            # Get the id of the 100th most recent entry
            result_rows = await conn.execute(
                select(sync_state_table.c.id)
                .order_by(sync_state_table.c.timestamp.desc())
                .offset(100)
                .limit(1)
            )
            cutoff_row = result_rows.scalar_one_or_none()
            if cutoff_row is not None:
                await conn.execute(
                    delete(sync_state_table).where(sync_state_table.c.id <= cutoff_row)
                )

    async def get_sync_history(self) -> list[dict]:
        """Return the sync history log."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(sync_state_table).order_by(sync_state_table.c.timestamp.desc()).limit(100)
            )
            rows = result.mappings().all()
        # Return in chronological order (oldest first) to match old behavior
        return [self._row_to_sync_dict(row) for row in reversed(rows)]

    @staticmethod
    def _row_to_sync_dict(row) -> dict:
        """Convert a sync_state DB row to the dict format used by the API."""
        row_dict = dict(row)
        result = {
            "type": row_dict.get("sync_type", "unknown"),
            "timestamp": (
                row_dict["timestamp"].isoformat()
                if hasattr(row_dict.get("timestamp"), "isoformat")
                else str(row_dict.get("timestamp", ""))
            ),
            "started_at": (
                row_dict["started_at"].isoformat()
                if hasattr(row_dict.get("started_at"), "isoformat")
                else str(row_dict.get("started_at", ""))
            ),
            "watermark": (
                row_dict["watermark"].isoformat()
                if hasattr(row_dict.get("watermark"), "isoformat")
                else str(row_dict.get("watermark", ""))
            ),
            "change_set_id": row_dict.get("change_set_id"),
            "total_docs": row_dict.get("total_docs", 0),
            "workers": row_dict.get("workers", {}),
        }
        # Merge in any extra details (since, full_connectors, etc.)
        details = row_dict.get("details", {})
        if details:
            result.update(details)
        return result
