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

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
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
    ContributionSubmitRequest,
    ContributionSubmitResponse,
    ContributionResponse,
    ContributionListResponse,
    ContributionUpdateRequest,
    ContributionActionRequest,
    ContributionActionResponse,
    RegisterRequest,
    GoogleAuthRequest,
    LoginRequest,
    AuthResponse,
    UserListResponse,
    ApproveUserRequest,
    UpdateRoleRequest,
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
    contribution_manager=None,
    user_manager=None,
    google_client_id: str = "",
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

    # ── Session auth dependency ────────────────────────────

    async def get_current_user(request: Request):
        """Extract and verify session token from Authorization header."""
        if not user_manager:
            return None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return user_manager.verify_token(token)
        return None

    # ── Auth endpoints ─────────────────────────────────────

    @app.post("/api/auth/register", response_model=AuthResponse)
    async def register_email(request: RegisterRequest):
        """Register a new user via email."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        if request.password != request.confirm_password:
            return AuthResponse(error="Passwords do not match")
        result = user_manager.register_email(
            first_name=request.first_name,
            last_name=request.last_name,
            dob=request.dob,
            email=request.email,
            password=request.password,
        )
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(user=result["user"], pending=True)

    @app.post("/api/auth/register/google", response_model=AuthResponse)
    async def register_google(request: GoogleAuthRequest):
        """Register or login a user via Google."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = await user_manager.register_google(request.id_token)
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(
            user=result.get("user", {}),
            token=result.get("token"),
            pending=result.get("pending", False),
        )

    @app.post("/api/auth/login", response_model=AuthResponse)
    async def login_email(request: LoginRequest):
        """Login via email + password."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = user_manager.login_email(request.email, request.password)
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(
            user=result.get("user", {}),
            token=result.get("token"),
            pending=result.get("pending", False),
        )

    @app.post("/api/auth/login/google", response_model=AuthResponse)
    async def login_google(request: GoogleAuthRequest):
        """Login via Google ID token."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = await user_manager.login_google(request.id_token)
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(
            user=result.get("user", {}),
            token=result.get("token"),
            pending=result.get("pending", False),
        )

    @app.get("/api/auth/me")
    async def get_me(request: Request):
        """Get current user profile from session token."""
        user = await get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return user

    @app.get("/api/auth/config")
    async def get_auth_config():
        """Return public auth configuration (e.g. whether Google is enabled)."""
        return {
            "google_enabled": bool(google_client_id),
            "google_client_id": google_client_id if google_client_id else None,
        }

    # ── Admin User Management ──────────────────────────────

    @app.get("/api/admin/users", dependencies=[Depends(require_admin)])
    async def list_users():
        """List all registered users."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        users = user_manager.list_users()
        return {"users": users, "count": len(users)}

    @app.post("/api/admin/users/{user_id}/approve", dependencies=[Depends(require_admin)])
    async def approve_user(user_id: str, request: ApproveUserRequest):
        """Approve a pending user and assign a role."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = user_manager.approve_user(user_id, request.role)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.post("/api/admin/users/{user_id}/reject", dependencies=[Depends(require_admin)])
    async def reject_user(user_id: str):
        """Reject a pending user."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = user_manager.reject_user(user_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.put("/api/admin/users/{user_id}/role", dependencies=[Depends(require_admin)])
    async def change_user_role(user_id: str, request: UpdateRoleRequest):
        """Change an approved user's role."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        result = user_manager.update_role(user_id, request.role)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

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

    # ── Contribution endpoints ─────────────────────────────

    @app.post("/api/contributions/submit")
    async def submit_contribution(request: ContributionSubmitRequest, req: Request):
        """User submits a contribution request (public endpoint)."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        # Auto-populate name from session if logged in
        current_user = await get_current_user(req)
        submitted_by = request.submitted_by
        if current_user:
            submitted_by = f"{current_user['first_name']} {current_user['last_name']}".strip()
        if not submitted_by or not submitted_by.strip():
            raise HTTPException(status_code=422, detail="Name is required")
        result = contribution_manager.submit(
            title=request.title,
            content=request.content,
            content_type=request.content_type,
            submitted_by=request.submitted_by,
        )
        response = JSONResponse(content={
            "id": result["id"],
            "status": "pending",
            "message": "Contribution submitted for review",
        })
        # Set cookie so "My Submissions" can identify the user
        response.set_cookie(
            key="grasp_user",
            value=request.submitted_by.strip(),
            max_age=60 * 60 * 24 * 365,  # 1 year
            httponly=False,
            samesite="lax",
        )
        return response

    @app.post("/api/contributions/upload")
    async def upload_contribution(
        req: Request,
        file: UploadFile = File(...),
        title: str = Form(...),
        submitted_by: str = Form(""),
    ):
        """User uploads a document file (.txt, .md, .pdf, .docx) as a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        # Auto-populate name from session if logged in
        current_user = await get_current_user(req)
        if current_user:
            submitted_by = f"{current_user['first_name']} {current_user['last_name']}".strip()
        if not submitted_by or not submitted_by.strip():
            raise HTTPException(status_code=422, detail="Name is required")

        # Validate file extension
        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("txt", "md", "pdf", "docx"):
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type '.{ext}'. Allowed: .txt, .md, .pdf, .docx",
            )

        # Read file content
        file_bytes = await file.read()

        # Extract text based on file type
        try:
            if ext in ("txt", "md"):
                content = file_bytes.decode("utf-8", errors="replace")
            elif ext == "pdf":
                import io
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                content = "\n\n".join(pages)
            elif ext == "docx":
                import io
                from docx import Document as DocxDocument
                doc = DocxDocument(io.BytesIO(file_bytes))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                content = "\n\n".join(paragraphs)
            else:
                content = ""

            if not content.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Could not extract any text from the uploaded file",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"File parsing error: {e}")
            raise HTTPException(
                status_code=422,
                detail=f"Failed to parse file: {e}",
            )

        # Use filename as title if title is empty
        final_title = title.strip() or filename.rsplit(".", 1)[0]

        result = contribution_manager.submit(
            title=final_title,
            content=content,
            content_type="document",
            submitted_by=submitted_by,
            original_filename=filename,
            original_file_ext=ext,
        )

        # Save original file bytes alongside the contribution JSON
        try:
            original_path = contribution_manager.contributions_dir / f"{result['id']}_original.{ext}"
            original_path.write_bytes(file_bytes)
            logger.info(f"Saved original file: {original_path.name}")
        except Exception as e:
            logger.warning(f"Could not save original file: {e}")

        response = JSONResponse(content={
            "id": result["id"],
            "status": "pending",
            "message": f"Document uploaded ({len(content)} chars extracted)",
        })
        # Set cookie so "My Submissions" can identify the user
        response.set_cookie(
            key="grasp_user",
            value=submitted_by.strip(),
            max_age=60 * 60 * 24 * 365,  # 1 year
            httponly=False,
            samesite="lax",
        )
        return response

    @app.get("/api/contributions/pending", response_model=ContributionListResponse, dependencies=[Depends(require_admin)])
    async def get_pending_contributions():
        """List all pending contributions (admin only)."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        pending = contribution_manager.list_pending()
        return ContributionListResponse(contributions=pending, count=len(pending))

    @app.get("/api/contributions/count", dependencies=[Depends(require_admin)])
    async def get_contribution_count():
        """Get count of pending contributions (for badge polling)."""
        if not contribution_manager:
            return {"count": 0}
        return {"count": contribution_manager.count_pending()}

    @app.get("/api/contributions/my")
    async def get_my_contributions(request: Request, submitted_by: str = ""):
        """Public endpoint — list all contributions for a given submitter name.

        Accepts the name via query param or ``grasp_user`` cookie.
        """
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        name = submitted_by.strip()
        # Fallback to cookie if no query param provided
        if not name:
            name = (request.cookies.get("grasp_user") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="submitted_by is required")
        all_contributions = contribution_manager.list_all()
        mine = [c for c in all_contributions if c.get("submitted_by", "").strip().lower() == name.lower()]
        return {"contributions": mine, "count": len(mine)}

    @app.get("/api/contributions/{contribution_id}/download")
    async def download_contribution_file(contribution_id: str):
        """Download the original uploaded file for a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        contribution = contribution_manager.get(contribution_id)
        if not contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        ext = contribution.get("original_file_ext", "")
        if not ext:
            raise HTTPException(status_code=404, detail="No original file attached")
        original_path = contribution_manager.contributions_dir / f"{contribution_id}_original.{ext}"
        if not original_path.exists():
            raise HTTPException(status_code=404, detail="Original file not found on disk")
        original_filename = contribution.get("original_filename", f"{contribution_id}.{ext}")
        return FileResponse(
            path=str(original_path),
            filename=original_filename,
            media_type="application/octet-stream",
        )

    @app.get("/api/contributions/{contribution_id}", response_model=ContributionResponse, dependencies=[Depends(require_admin)])
    async def get_contribution(contribution_id: str):
        """Get a single contribution by ID (admin only)."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        contribution = contribution_manager.get(contribution_id)
        if not contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        return ContributionResponse(**contribution)

    @app.put("/api/contributions/{contribution_id}", response_model=ContributionResponse, dependencies=[Depends(require_admin)])
    async def update_contribution(contribution_id: str, request: ContributionUpdateRequest):
        """Admin edits the contribution content before approval."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        result = contribution_manager.update_content(
            contribution_id,
            title=request.title,
            content=request.content,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Contribution not found or not pending")
        return ContributionResponse(**result)

    @app.post("/api/contributions/{contribution_id}/approve", response_model=ContributionActionResponse, dependencies=[Depends(require_admin)])
    async def approve_contribution(contribution_id: str, request: ContributionActionRequest):
        """Approve a contribution — classify and write to the repo."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        result = await contribution_manager.approve(
            contribution_id,
            admin_notes=request.admin_notes,
        )
        if "error" in result:
            return ContributionActionResponse(status="error", message=result["error"])
        return ContributionActionResponse(**result)

    @app.post("/api/contributions/{contribution_id}/reject", response_model=ContributionActionResponse, dependencies=[Depends(require_admin)])
    async def reject_contribution(contribution_id: str, request: ContributionActionRequest):
        """Reject a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        result = contribution_manager.reject(
            contribution_id,
            admin_notes=request.admin_notes,
        )
        if "error" in result:
            return ContributionActionResponse(status="error", message=result["error"])
        return ContributionActionResponse(**result)

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

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        """Serve the login/register page."""
        html_path = static_dir / "login.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Grasp Login</h1><p>Login page not found.</p>")

    return app
