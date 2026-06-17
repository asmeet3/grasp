"""Sync orchestrator — coordinates parallel retrieval across all connectors.

Handles full sync (checkpointed), incremental sync, parallel worker
execution, and pending changeset generation for human approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..connectors.base import BaseConnector, Document
from ..index.vector_store import VectorStore
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
        vector_store: VectorStore,
        checkpoints: CheckpointManager,
        state_dir: Path,
    ):
        self.connectors = connectors
        self.repo_manager = repo_manager
        self.vector_store = vector_store
        self.checkpoints = checkpoints
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._sync_running = False
        self._worker_statuses: dict[str, WorkerStatus] = {}

    # ── Public interface ───────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._sync_running

    @property
    def worker_statuses(self) -> dict[str, dict]:
        return {name: ws.to_dict() for name, ws in self._worker_statuses.items()}

    def get_last_sync(self) -> dict | None:
        """Read the last sync state."""
        path = self.state_dir / "last_sync.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def needs_full_sync(self) -> bool:
        """Check if a full (initial) sync is required."""
        return self.get_last_sync() is None

    async def run_sync(self) -> dict:
        """Run a sync — full or incremental depending on state.

        If the last sync had any failed connectors, those connectors
        get a full sync while successful ones get incremental.
        """
        if self._sync_running:
            return {"error": "Sync already in progress"}

        self._sync_running = True
        self._worker_statuses = {}

        try:
            if self.needs_full_sync():
                logger.info("Starting FULL sync (no previous sync found)")
                result = await self._full_sync()
            else:
                last_sync = self.get_last_sync()
                since_str = last_sync["timestamp"]
                since = datetime.fromisoformat(since_str)

                # Check which connectors failed last time
                last_workers = last_sync.get("workers", {})
                failed_connectors = {
                    name for name, info in last_workers.items()
                    if info.get("status") == "failed"
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

            # Save sync state
            self._save_sync_state(result)

            # Generate pending changeset for human approval
            self.repo_manager.stage_pending()

            return result
        except Exception as e:
            logger.error(f"Sync failed: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}
        finally:
            self._sync_running = False

    # ── Full sync ──────────────────────────────────────────

    async def _full_sync(self) -> dict:
        """Run a full sync with all connectors in parallel."""
        tasks = []
        for name, connector in self.connectors.items():
            checkpoint = self.checkpoints.load_checkpoint(name)
            ws = WorkerStatus(name)
            self._worker_statuses[name] = ws
            tasks.append(self._run_full_worker(connector, ws, checkpoint))

        # Run all workers in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        total_docs = 0
        worker_results = {}
        for name, result in zip(self.connectors.keys(), results):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                ws.errors.append(str(result))
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}
                self.checkpoints.clear_checkpoint(name)

        return {
            "type": "full",
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _mixed_sync(self, since: datetime, full_sync_connectors: set[str]) -> dict:
        """Run full sync for failed connectors and incremental for the rest."""
        tasks = []
        for name, connector in self.connectors.items():
            ws = WorkerStatus(name)
            self._worker_statuses[name] = ws

            if name in full_sync_connectors:
                checkpoint = self.checkpoints.load_checkpoint(name)
                tasks.append(self._run_full_worker(connector, ws, checkpoint))
            else:
                tasks.append(self._run_incremental_worker(connector, ws, since))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_docs = 0
        worker_results = {}
        for name, result in zip(self.connectors.keys(), results):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                ws.errors.append(str(result))
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}
                if name in full_sync_connectors:
                    self.checkpoints.clear_checkpoint(name)

        return {
            "type": "mixed",
            "since": since.isoformat(),
            "full_connectors": list(full_sync_connectors),
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
                self.checkpoints.save_checkpoint(connector.name, state)

            ws.status = "completed"
        except Exception as e:
            ws.status = "failed"
            ws.errors.append(f"{type(e).__name__}: {e}")
            logger.error(f"Worker {connector.name} failed: {e}\n{traceback.format_exc()}")
            raise
        finally:
            ws.completed_at = time.time()

    # ── Incremental sync ───────────────────────────────────

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
        for name, result in zip(self.connectors.keys(), results):
            ws = self._worker_statuses[name]
            if isinstance(result, Exception):
                ws.status = "failed"
                ws.errors.append(str(result))
                worker_results[name] = {"status": "failed", "error": str(result)}
            else:
                total_docs += ws.docs_fetched
                worker_results[name] = {"status": "completed", "docs": ws.docs_fetched}

        return {
            "type": "incremental",
            "since": since.isoformat(),
            "total_docs": total_docs,
            "workers": worker_results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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

    # ── Document processing ────────────────────────────────

    async def _process_document(self, doc: Document):
        """Write a document to the repo and index it in ChromaDB."""
        try:
            # Classify and write to repo
            info_type = await self.repo_manager.classify_and_write(doc)

            # Index in vector store
            self.vector_store.index_document(doc, info_type)

        except Exception as e:
            logger.error(f"Failed to process document {doc.id}: {e}")

    # ── State management ───────────────────────────────────

    def _save_sync_state(self, result: dict):
        """Save the sync result as last_sync state."""
        path = self.state_dir / "last_sync.json"
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

        # Append to sync log
        log_path = self.state_dir / "sync_log.json"
        log_entries = []
        if log_path.exists():
            try:
                log_entries = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                log_entries = []

        log_entries.append(result)
        # Keep last 100 entries
        log_entries = log_entries[-100:]
        log_path.write_text(json.dumps(log_entries, indent=2, default=str), encoding="utf-8")

    def get_sync_history(self) -> list[dict]:
        """Return the sync history log."""
        log_path = self.state_dir / "sync_log.json"
        if log_path.exists():
            try:
                return json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []
