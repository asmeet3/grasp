"""FastAPI server — REST API and web dashboard for Grasp.

Provides endpoints for querying the institutional brain, managing syncs,
reviewing pending changes, and monitoring system health.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from sse_starlette.sse import EventSourceResponse

from .models import (
    QueryRequest,
    SyncTriggerResponse,
    SyncStatusResponse,
    PendingChangesResponse,
    ApproveRequest,
    ApproveResponse,
    RejectResponse,
    SystemStatusResponse,
    SourcesResponse,
)

logger = logging.getLogger(__name__)


def create_app(
    query_engine,
    sync_orchestrator,
    sync_scheduler,
    repo_manager,
    vector_store,
    connectors: dict,
    admin_key: str = "",
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Grasp — Institutional Brain",
        description="Agentic AI that answers questions about your organization",
        version="1.0.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static files
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Admin auth dependency ──────────────────────────────

    _admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

    async def require_admin(key: str = Depends(_admin_key_header)):
        if not admin_key or not key or key != admin_key:
            raise HTTPException(status_code=403, detail="Invalid or missing admin key")

    # ── Query endpoint (SSE streaming) ─────────────────────

    @app.post("/api/query")
    async def query(request: QueryRequest):
        """Submit a question and get a streamed answer via SSE."""
        async def event_generator():
            try:
                async for chunk in query_engine.query_stream(request.question):
                    yield {"event": "chunk", "data": chunk}
                yield {"event": "done", "data": ""}
            except Exception as e:
                logger.error(f"Query error: {e}")
                yield {"event": "error", "data": str(e)}

        return EventSourceResponse(event_generator())

    # ── Sync endpoints ─────────────────────────────────────

    # ── Health check cache ──────────────────────────────────
    _health_cache: dict = {}
    _health_cache_ts: float = 0.0
    _HEALTH_TTL: float = 300.0  # 5 minutes

    @app.get("/api/status", response_model=SystemStatusResponse)
    async def get_status():
        """Get system status overview (health checks cached for 5 min)."""
        nonlocal _health_cache, _health_cache_ts

        now = time.time()
        if now - _health_cache_ts > _HEALTH_TTL or not _health_cache:
            # Refresh health checks
            async def check_health(name, connector):
                try:
                    result = await asyncio.wait_for(connector.health_check(), timeout=5.0)
                    return name, result
                except Exception:
                    return name, False

            results = await asyncio.gather(
                *(check_health(name, conn) for name, conn in connectors.items())
            )
            _health_cache = dict(results)
            _health_cache_ts = now

        return SystemStatusResponse(
            status="syncing" if sync_orchestrator.is_running else "online",
            last_sync=sync_orchestrator.get_last_sync(),
            next_scheduled=sync_scheduler.next_run_time,
            connector_health=_health_cache,
            document_stats=repo_manager.get_source_stats(),
            vector_index=vector_store.get_stats(),
        )

    @app.post("/api/sync/trigger", response_model=SyncTriggerResponse, dependencies=[Depends(require_admin)])
    async def trigger_sync():
        """Manually trigger a sync."""
        if sync_orchestrator.is_running:
            return SyncTriggerResponse(status="already_running", message="Sync already in progress")

        # Run sync in background
        asyncio.create_task(sync_orchestrator.run_sync())
        return SyncTriggerResponse(status="started", message="Sync triggered")

    @app.get("/api/sync/status", response_model=SyncStatusResponse, dependencies=[Depends(require_admin)])
    async def sync_status():
        """Get current sync status including worker progress."""
        return SyncStatusResponse(
            is_running=sync_orchestrator.is_running,
            last_sync=sync_orchestrator.get_last_sync(),
            next_scheduled=sync_scheduler.next_run_time,
            workers=sync_orchestrator.worker_statuses if sync_orchestrator.is_running else None,
        )

    @app.get("/api/sync/history", dependencies=[Depends(require_admin)])
    async def sync_history():
        """Get sync history log."""
        return sync_orchestrator.get_sync_history()

    # ── Pending changes endpoints ──────────────────────────

    @app.get("/api/changes/pending", response_model=PendingChangesResponse, dependencies=[Depends(require_admin)])
    async def get_pending_changes():
        """Get current pending changeset for review."""
        changes = repo_manager.get_pending_changes()
        return PendingChangesResponse(
            has_pending=changes is not None,
            changeset=changes,
        )

    @app.get("/api/changes/diff/{file_path:path}", dependencies=[Depends(require_admin)])
    async def get_file_diff(file_path: str):
        """Get the diff for a specific pending file."""
        diff = repo_manager.get_file_diff(file_path)
        return {"file_path": file_path, "diff": diff}

    @app.post("/api/changes/approve", response_model=ApproveResponse, dependencies=[Depends(require_admin)])
    async def approve_changes(request: ApproveRequest):
        """Approve and commit all pending changes."""
        result = repo_manager.approve_commit(request.message)
        if "error" in result:
            return ApproveResponse(status="error", error=result["error"])
        return ApproveResponse(**result)

    @app.post("/api/changes/reject", response_model=RejectResponse, dependencies=[Depends(require_admin)])
    async def reject_changes():
        """Reject and revert all pending changes."""
        result = repo_manager.reject_changes()
        if "error" in result:
            return RejectResponse(status="error", error=result["error"])
        return RejectResponse(status="rejected")

    # ── Sources endpoint ───────────────────────────────────

    @app.get("/api/sources", response_model=SourcesResponse)
    async def get_sources():
        """Get document counts per source and type."""
        return SourcesResponse(sources=repo_manager.get_source_stats())

    # ── Web pages ─────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def user_page():
        """Serve the user Q&A page."""
        html_path = static_dir / "index.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Grasp</h1><p>Static files not found.</p>")

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        """Serve the admin dashboard."""
        html_path = static_dir / "admin.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Grasp Admin</h1><p>Admin page not found.</p>")

    return app
