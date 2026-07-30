"""FastAPI server — REST API and web dashboard for Grasp.

Provides endpoints for querying the institutional brain, managing syncs,
reviewing pending changes, and monitoring system health.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..agents import SUPPORTED_SKILLS, AgentDefinition
from ..connectors.base import sanitize_filename
from ..core.security import AuthContext, Permission, PolicyEngine, SystemRole
from .models import (
    AgentActivationRequest,
    AgentEmergencyStopRequest,
    AgentRunRequest,
    ApproveRequest,
    ApproveResponse,
    ApproveUserRequest,
    AuthResponse,
    ChangePasswordRequest,
    ChatThreadListResponse,
    ContributionActionRequest,
    ContributionActionResponse,
    ContributionListResponse,
    ContributionResponse,
    ContributionSubmitRequest,
    ContributionUpdateRequest,
    DeleteAccountRequest,
    EntityDetailResponse,
    EntityListResponse,
    EntityResponse,
    EntityReviewRequest,
    GoogleAuthRequest,
    LoginRequest,
    MemoryExtractRequest,
    MemoryStatsResponse,
    PendingChangesResponse,
    QueryRequest,
    RegisterRequest,
    RejectRequest,
    RejectResponse,
    RelationshipResponse,
    SaveChatThreadRequest,
    SourcesResponse,
    SyncStatusResponse,
    SyncTriggerResponse,
    SystemStatusResponse,
    UpdateProfileRequest,
    UpdateRoleRequest,
    WorkItemListResponse,
    WorkItemResponse,
    WorkItemStatusRequest,
)
from .security import SlidingWindowRateLimiter

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
    chat_manager=None,
    google_client_id: str = "",
    change_set_service=None,
    policy_engine: PolicyEngine | None = None,
    trusted_origins: list[str] | None = None,
    upload_max_bytes: int = 10 * 1024 * 1024,
    upload_max_pages: int = 200,
    upload_max_text_chars: int = 2_000_000,
    auth_rate_limit: int = 20,
    query_rate_limit: int = 60,
    upload_rate_limit: int = 10,
    job_queue=None,
    metrics=None,
    agent_service=None,
    agent_scheduler=None,
    memory_service=None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Grasp — Institutional Brain",
        description="Agentic AI that answers questions about your organization",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=trusted_origins or ["http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    icons_dir = Path(__file__).parent.parent / "icons"
    if icons_dir.exists():
        app.mount("/icons", StaticFiles(directory=str(icons_dir)), name="icons")

    logos_dir = Path(__file__).parent.parent / "logos"
    if logos_dir.exists():
        app.mount("/logos", StaticFiles(directory=str(logos_dir)), name="logos")

    # Authentication dependencies

    _admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
    policy = policy_engine or PolicyEngine()
    rate_limiter = SlidingWindowRateLimiter()

    async def enforce_rate(request: Request, bucket: str, limit: int) -> None:
        client = request.client.host if request.client else "unknown"
        if not await rate_limiter.allow(f"{bucket}:{client}", limit):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    async def get_current_user(request: Request):
        """Extract and verify session token from Authorization header."""
        if not user_manager:
            return None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return await user_manager.verify_token(token)
        return None

    async def require_context(request: Request, permission: Permission) -> AuthContext:
        user = await get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        context = AuthContext.from_user(user)
        try:
            policy.require(context, permission)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return context

    def valid_bootstrap_key(key: str | None) -> bool:
        return bool(admin_key and key and hmac.compare_digest(key, admin_key))

    async def require_administrator(request: Request) -> AuthContext:
        """Require an authenticated administrator; bootstrap keys never satisfy this."""
        return await require_context(request, Permission.MANAGE_USERS)

    async def bootstrap_available(key: str | None) -> bool:
        """Allow the bootstrap secret only until the first administrator exists."""
        if not valid_bootstrap_key(key) or not user_manager:
            return False
        return await user_manager.count_administrators() == 0

    async def require_administrator_or_bootstrap(
        request: Request,
        key: str | None,
    ) -> AuthContext | None:
        user = await get_current_user(request)
        if user:
            context = AuthContext.from_user(user)
            if policy.allows(context, Permission.MANAGE_USERS):
                return context
        if await bootstrap_available(key):
            return None
        raise HTTPException(status_code=403, detail="Administrator permission required")

    async def managed_user(user_id: str, context: AuthContext) -> dict:
        """Resolve a target without exposing or mutating users in another organization."""
        users = await user_manager.list_users(context.organization_id)
        target = next((user for user in users if user.get("id") == user_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        return target

    # Authentication

    @app.post("/api/auth/register", response_model=AuthResponse)
    async def register_email(request: RegisterRequest, req: Request):
        """Register a new user via email."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        await enforce_rate(req, "auth", auth_rate_limit)
        if request.password != request.confirm_password:
            return AuthResponse(error="Passwords do not match")
        result = await user_manager.register_email(
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
    async def register_google(request: GoogleAuthRequest, req: Request):
        """Register or login a user via Google."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        await enforce_rate(req, "auth", auth_rate_limit)
        result = await user_manager.register_google(request.id_token)
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(
            user=result.get("user", {}),
            token=result.get("token"),
            pending=result.get("pending", False),
        )

    @app.post("/api/auth/login", response_model=AuthResponse)
    async def login_email(request: LoginRequest, req: Request):
        """Login via email + password."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        await enforce_rate(req, "auth", auth_rate_limit)
        result = await user_manager.login_email(request.email, request.password)
        if "error" in result:
            return AuthResponse(error=result["error"], conflict=result.get("conflict"))
        return AuthResponse(
            user=result.get("user", {}),
            token=result.get("token"),
            pending=result.get("pending", False),
        )

    @app.post("/api/auth/login/google", response_model=AuthResponse)
    async def login_google(request: GoogleAuthRequest, req: Request):
        """Login via Google ID token."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        await enforce_rate(req, "auth", auth_rate_limit)
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

    @app.put("/api/auth/profile")
    async def update_profile(request: UpdateProfileRequest, req: Request):
        """Update the current user's profile (name, dob, profile picture)."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        result = await user_manager.update_profile(
            user_id=user["id"],
            first_name=request.first_name,
            last_name=request.last_name,
            dob=request.dob,
            profile_picture=request.profile_picture,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result["user"]

    @app.put("/api/auth/password")
    async def change_password(request: ChangePasswordRequest, req: Request):
        """Change the current user's password. Invalidates all existing sessions."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if request.new_password != request.confirm_new_password:
            raise HTTPException(status_code=422, detail="New passwords do not match")
        result = await user_manager.change_password(
            user_id=user["id"],
            current_password=request.current_password,
            new_password=request.new_password,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"message": result["message"], "logout_required": True}

    @app.delete("/api/auth/account")
    async def delete_account(request: DeleteAccountRequest, req: Request):
        """Permanently delete the current user's account.

        Email accounts must supply their password for verification.
        Google accounts are verified through their active session token.
        """
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        result = await user_manager.delete_account(
            user_id=user["id"],
            password=request.password,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"message": result["message"], "logout_required": True}

    # Chat history

    @app.get("/api/chats", response_model=ChatThreadListResponse)
    async def get_chats(req: Request):
        """Get all chat threads for the current user."""
        if not chat_manager:
            raise HTTPException(status_code=503, detail="Chat manager not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        threads = await chat_manager.get_user_chats(user["id"])
        return ChatThreadListResponse(threads=threads)

    @app.post("/api/chats")
    async def save_chat(request: SaveChatThreadRequest, req: Request):
        """Save or update a chat thread."""
        if not chat_manager:
            raise HTTPException(status_code=503, detail="Chat manager not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        await chat_manager.save_chat(
            user_id=user["id"],
            chat_id=request.id,
            title=request.title,
            messages=request.messages,
            created_at=request.created_at,
        )
        return {"status": "ok"}

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str, req: Request):
        """Delete a chat thread."""
        if not chat_manager:
            raise HTTPException(status_code=503, detail="Chat manager not available")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        deleted = await chat_manager.delete_chat(user["id"], chat_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat not found or access denied")
        return {"status": "ok"}

    # User administration

    @app.get("/api/admin/bootstrap/status")
    async def admin_bootstrap_status():
        """Report whether first-run administrator bootstrap is still required."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        return {
            "bootstrap_required": await user_manager.count_administrators() == 0,
            "bootstrap_configured": bool(admin_key),
        }

    @app.get("/api/admin/access")
    async def admin_access(req: Request, key: str = Depends(_admin_key_header)):
        """Validate an operations session or one-time administrator bootstrap."""
        user = await get_current_user(req)
        if user:
            context = AuthContext.from_user(user)
            if policy.allows(context, Permission.MANAGE_USERS) or policy.allows(
                context, Permission.MANAGE_AGENTS
            ):
                return {"authenticated": True, "bootstrap": False}
        if await bootstrap_available(key):
            return {"authenticated": True, "bootstrap": True}
        raise HTTPException(status_code=403, detail="Operator or Administrator permission required")

    @app.post("/api/admin/bootstrap/claim")
    async def claim_bootstrap_administrator(
        req: Request,
        key: str = Depends(_admin_key_header),
    ):
        """Promote a signed-in user while the one-time bootstrap window is open."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        if not await bootstrap_available(key):
            raise HTTPException(status_code=403, detail="Administrator bootstrap is unavailable")
        user = await get_current_user(req)
        if not user:
            raise HTTPException(
                status_code=401, detail="Sign in before claiming administrator access"
            )
        job_title = user.get("job_title") or user.get("role") or "Associate"
        result = await user_manager.update_role(
            user["id"],
            job_title,
            SystemRole.ADMINISTRATOR.value,
            actor_user_id=user["id"],
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.get("/api/admin/users")
    async def list_users(req: Request, key: str = Depends(_admin_key_header)):
        """List users in the administrator's organization or during bootstrap."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        context = await require_administrator_or_bootstrap(req, key)
        users = await user_manager.list_users(context.organization_id if context else None)
        return {"users": users, "count": len(users)}

    @app.post("/api/admin/users/{user_id}/approve")
    async def approve_user(
        user_id: str,
        request: ApproveUserRequest,
        req: Request,
        key: str = Depends(_admin_key_header),
    ):
        """Approve a pending user and assign a role."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        context = await require_administrator_or_bootstrap(req, key)
        if context:
            await managed_user(user_id, context)
            system_role = request.system_role
            actor_user_id = context.user_id
        else:
            if request.system_role != SystemRole.ADMINISTRATOR.value:
                raise HTTPException(
                    status_code=403,
                    detail="The first approved account must be an administrator",
                )
            system_role = SystemRole.ADMINISTRATOR.value
            actor_user_id = None
        result = await user_manager.approve_user(
            user_id,
            request.role,
            system_role,
            actor_user_id=actor_user_id,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.post("/api/admin/users/{user_id}/reject")
    async def reject_user(user_id: str, req: Request):
        """Reject a pending user."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        context = await require_administrator(req)
        await managed_user(user_id, context)
        if user_id == context.user_id:
            raise HTTPException(
                status_code=409,
                detail="Administrators cannot revoke their own access",
            )
        result = await user_manager.reject_user(user_id, actor_user_id=context.user_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.put("/api/admin/users/{user_id}/role")
    async def change_user_role(user_id: str, request: UpdateRoleRequest, req: Request):
        """Change an approved user's role."""
        if not user_manager:
            raise HTTPException(status_code=503, detail="Authentication not available")
        context = await require_administrator(req)
        await managed_user(user_id, context)
        if user_id == context.user_id and request.system_role is not None:
            raise HTTPException(
                status_code=409,
                detail="Administrators cannot change their own access level",
            )
        result = await user_manager.update_role(
            user_id,
            request.role,
            request.system_role,
            actor_user_id=context.user_id,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    # Query streaming

    @app.post("/api/query")
    async def query(request: QueryRequest, req: Request):
        """Submit a question and get a streamed answer via SSE."""
        context = await require_context(req, Permission.QUERY)
        await enforce_rate(req, f"query:{context.user_id}", query_rate_limit)
        history = None
        if request.history:
            history = [{"role": m.role, "content": m.content} for m in request.history]

        async def event_generator():
            try:
                async for chunk in query_engine.query_stream(
                    request.question, history=history, auth_context=context
                ):
                    yield {"event": "chunk", "data": chunk}
                yield {"event": "done", "data": ""}
            except Exception:
                logger.exception("Query execution failed")
                yield {"event": "error", "data": "The query could not be completed."}

        return EventSourceResponse(event_generator())

    # Sync and health
    _health_cache: dict = {}
    _health_cache_ts: float = 0.0
    _HEALTH_TTL: float = 300.0  # 5 minutes

    @app.get("/api/health/live")
    async def liveness():
        """Unauthenticated process liveness for orchestrators."""
        return {"status": "ok"}

    @app.get("/api/status", response_model=SystemStatusResponse)
    async def get_status(req: Request):
        """Get system status overview (health checks cached for 5 min)."""
        await require_context(req, Permission.QUERY)
        nonlocal _health_cache, _health_cache_ts

        now = time.time()
        if now - _health_cache_ts > _HEALTH_TTL or not _health_cache:

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

        document_stats, vector_stats = await asyncio.gather(
            asyncio.to_thread(repo_manager.get_source_stats),
            asyncio.to_thread(vector_store.get_stats),
        )
        return SystemStatusResponse(
            status="syncing" if sync_orchestrator.is_running else "online",
            last_sync=await sync_orchestrator.get_last_sync(),
            next_scheduled=sync_scheduler.next_run_time,
            connector_health=_health_cache,
            document_stats=document_stats,
            vector_index=vector_stats,
        )

    @app.post("/api/sync/trigger", response_model=SyncTriggerResponse)
    async def trigger_sync(req: Request):
        """Manually trigger a sync."""
        await require_context(req, Permission.REVIEW)
        if sync_orchestrator.is_running:
            return SyncTriggerResponse(status="already_running", message="Sync already in progress")

        if job_queue:
            job_id = await job_queue.enqueue(
                "sync",
                {"trigger": "manual", "actor_user_id": (await get_current_user(req))["id"]},
                idempotency_key=f"manual-sync:{uuid.uuid4()}",
            )
            return SyncTriggerResponse(status="queued", message=f"Sync job queued: {job_id}")
        asyncio.create_task(sync_orchestrator.run_sync())
        return SyncTriggerResponse(status="started", message="Sync triggered")

    @app.get("/api/sync/status", response_model=SyncStatusResponse)
    async def sync_status(req: Request):
        """Get current sync status including worker progress."""
        await require_context(req, Permission.REVIEW)
        return SyncStatusResponse(
            is_running=sync_orchestrator.is_running,
            last_sync=await sync_orchestrator.get_last_sync(),
            next_scheduled=sync_scheduler.next_run_time,
            workers=sync_orchestrator.worker_statuses if sync_orchestrator.is_running else None,
        )

    @app.get("/api/sync/history")
    async def sync_history(req: Request):
        """Get sync history log."""
        await require_context(req, Permission.REVIEW)
        return await sync_orchestrator.get_sync_history()

    # Pending changes

    @app.get("/api/changes/pending", response_model=PendingChangesResponse)
    async def get_pending_changes(req: Request):
        """Get current pending changeset for review."""
        context = await require_context(req, Permission.REVIEW)
        changes_list = (
            await change_set_service.list_pending(context.organization_id)
            if change_set_service
            else []
        )
        changes = changes_list[0] if changes_list else None
        if changes is not None:
            changes = dict(changes)
            changes["pending_count"] = len(changes_list)
            files = {"added": [], "modified": [], "deleted": []}
            by_type: dict[str, dict[str, int]] = {}
            for operation in changes.get("operations") or []:
                path = str(operation.get("path") or "")
                if operation.get("op") == "delete":
                    category = "deleted"
                elif repo_manager.read_committed_file(path, changes.get("base_commit_sha")):
                    category = "modified"
                else:
                    category = "added"
                files[category].append(path)
                layer = Path(path).parts[0] if path else "unknown"
                counts = by_type.setdefault(layer, {"added": 0, "modified": 0, "deleted": 0})
                counts[category] += 1
            changes["files"] = files
            changes["by_type"] = by_type
            changes["summary"] = {
                "total_added": len(files["added"]),
                "total_modified": len(files["modified"]),
                "total_deleted": len(files["deleted"]),
                "total_changes": sum(len(items) for items in files.values()),
            }
        return PendingChangesResponse(
            has_pending=changes is not None,
            changeset=changes,
        )

    @app.get("/api/changes/diff/{file_path:path}")
    async def get_file_diff(file_path: str, req: Request, change_set_id: str = ""):
        """Get the diff for a specific pending file."""
        context = await require_context(req, Permission.REVIEW)
        if not change_set_service:
            raise HTTPException(status_code=503, detail="Revision service unavailable")
        if not change_set_id:
            pending = await change_set_service.list_pending(context.organization_id)
            if not pending:
                return {"file_path": file_path, "diff": ""}
            change_set_id = pending[0]["id"]
        change_set = await change_set_service.get(change_set_id)
        if not change_set or change_set["organization_id"] != context.organization_id:
            raise HTTPException(status_code=404, detail="Change set not found")
        diff = await change_set_service.diff(change_set_id, file_path)
        return {"file_path": file_path, "diff": diff}

    @app.post("/api/changes/approve", response_model=ApproveResponse)
    async def approve_changes(request: ApproveRequest, req: Request):
        """Approve, commit, index, verify, and atomically activate a proposal."""
        context = await require_context(req, Permission.REVIEW)
        if not change_set_service:
            raise HTTPException(status_code=503, detail="Revision service unavailable")
        change_set_id = request.change_set_id
        if not change_set_id:
            pending = await change_set_service.list_pending(context.organization_id)
            pending = [item for item in pending if item["state"] in ("awaiting_review", "failed")]
            if not pending:
                return ApproveResponse(status="error", error="No pending change set")
            change_set_id = pending[0]["id"]
        try:
            result = await change_set_service.approve_and_activate(
                change_set_id,
                context,
                explanation=request.explanation,
                commit_message=request.message,
            )
            return ApproveResponse(
                status=result["state"],
                message="Committed, indexed, verified, and activated",
                changes={
                    "change_set_id": result["id"],
                    "commit_sha": result.get("final_commit_sha"),
                },
            )
        except Exception as exc:
            logger.exception("Change-set approval failed")
            return ApproveResponse(status="error", error=str(exc))

    @app.post("/api/changes/reject", response_model=RejectResponse)
    async def reject_changes(request: RejectRequest, req: Request):
        """Reject an isolated proposal without touching active knowledge."""
        context = await require_context(req, Permission.REVIEW)
        if not change_set_service:
            raise HTTPException(status_code=503, detail="Revision service unavailable")
        change_set_id = request.change_set_id
        if not change_set_id:
            pending = await change_set_service.list_pending(context.organization_id)
            if not pending:
                return RejectResponse(status="error", error="No pending change set")
            change_set_id = pending[0]["id"]
        try:
            await change_set_service.reject(change_set_id, context, explanation=request.explanation)
            return RejectResponse(status="rejected")
        except Exception as exc:
            return RejectResponse(status="error", error=str(exc))

    @app.get("/api/sources", response_model=SourcesResponse)
    async def get_sources(req: Request):
        """Get document counts per source and type."""
        await require_context(req, Permission.QUERY)
        return SourcesResponse(sources=await asyncio.to_thread(repo_manager.get_source_stats))

    @app.get("/api/admin/metrics")
    async def get_metrics(req: Request):
        await require_context(req, Permission.VIEW_AUDIT)
        return {"metrics": metrics.snapshot() if metrics else {}}

    # Governed company-brain agents

    def require_agent_service():
        if not agent_service:
            raise HTTPException(status_code=503, detail="Agent service is unavailable")
        return agent_service

    async def refresh_agent_schedule() -> None:
        if agent_scheduler:
            await agent_scheduler.refresh()

    @app.get("/api/agents/status")
    async def agent_status(req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        return await require_agent_service().control_status(context)

    @app.put("/api/agents/emergency-stop")
    async def set_agent_emergency_stop(request: AgentEmergencyStopRequest, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        service = require_agent_service()
        await service.set_emergency_stop(context, request.stopped, reason=request.reason)
        return await service.control_status(context)

    @app.get("/api/agents/templates")
    async def agent_templates(req: Request):
        await require_context(req, Permission.MANAGE_AGENTS)
        return {
            "skills": [
                {"name": name, "description": description}
                for name, description in SUPPORTED_SKILLS.items()
            ],
            "schedules": [
                {"name": "Manual only", "cron": None},
                {"name": "Every hour", "cron": "0 * * * *"},
                {"name": "Weekdays at 09:00 UTC", "cron": "0 9 * * 1-5"},
                {"name": "Every Monday at 09:00 UTC", "cron": "0 9 * * 1"},
            ],
        }

    @app.get("/api/agents/owners")
    async def agent_owners(req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        owners = await require_agent_service().list_owners(context)
        return {"owners": owners}

    @app.get("/api/agents/runs")
    async def agent_runs(req: Request, agent_id: str = "", limit: int = 50):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            runs = await require_agent_service().list_runs(
                context,
                agent_id=agent_id or None,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"runs": runs, "count": len(runs)}

    @app.get("/api/agents/runs/{run_id}")
    async def agent_run(run_id: str, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            return await require_agent_service().get_run(context, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/agents")
    async def list_agents(req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        agents = await require_agent_service().list_agents(context)
        if agent_scheduler:
            for agent in agents:
                agent["next_run_at"] = agent_scheduler.next_run_time(agent["id"])
        return {"agents": agents, "count": len(agents)}

    @app.post("/api/agents", status_code=201)
    async def create_agent(definition: AgentDefinition, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            agent_id = await require_agent_service().register(context, definition)
            await refresh_agent_schedule()
            return {"id": agent_id, "status": "created"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            agent = await require_agent_service().get_agent(context, agent_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if agent_scheduler:
            agent["next_run_at"] = agent_scheduler.next_run_time(agent_id)
        return agent

    @app.put("/api/agents/{agent_id}")
    async def update_agent(agent_id: str, definition: AgentDefinition, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            await require_agent_service().update_definition(context, agent_id, definition)
            await refresh_agent_schedule()
            return {"id": agent_id, "status": "updated"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/agents/{agent_id}/activation")
    async def activate_agent(
        agent_id: str,
        request: AgentActivationRequest,
        req: Request,
    ):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            await require_agent_service().set_active(context, agent_id, request.active)
            await refresh_agent_schedule()
            return {"id": agent_id, "active": request.active}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/agents/{agent_id}/run", status_code=202)
    async def run_agent(agent_id: str, request: AgentRunRequest, req: Request):
        context = await require_context(req, Permission.MANAGE_AGENTS)
        try:
            job_id = await require_agent_service().request_run(
                context,
                agent_id,
                prompt=request.prompt,
            )
            return {"status": "queued", "job_id": job_id}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Contributions

    @app.post("/api/contributions/submit")
    async def submit_contribution(request: ContributionSubmitRequest, req: Request):
        """Authenticated user submits a contribution request."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.CONTRIBUTE)
        await enforce_rate(req, f"upload:{context.user_id}", upload_rate_limit)
        current_user = await get_current_user(req)
        submitted_by = f"{current_user['first_name']} {current_user['last_name']}".strip()
        result = await contribution_manager.submit(
            title=request.title,
            content=request.content,
            content_type=request.content_type,
            submitted_by=submitted_by,
            submitter_user_id=context.user_id,
            organization_id=context.organization_id,
        )
        return JSONResponse(
            content={
                "id": result["id"],
                "status": "pending",
                "message": "Contribution submitted for review",
            }
        )

    @app.post("/api/contributions/upload")
    async def upload_contribution(
        req: Request,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()],
        submitted_by: Annotated[str, Form()] = "",
    ):
        """User uploads a document file (.txt, .md, .pdf, .docx) as a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.CONTRIBUTE)
        await enforce_rate(req, f"upload:{context.user_id}", upload_rate_limit)
        current_user = await get_current_user(req)
        submitted_by = f"{current_user['first_name']} {current_user['last_name']}".strip()

        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("txt", "md", "pdf", "docx"):
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type '.{ext}'. Allowed: .txt, .md, .pdf, .docx",
            )

        file_bytes = await file.read(upload_max_bytes + 1)
        if len(file_bytes) > upload_max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Uploaded file exceeds the configured size limit",
            )

        def extract_text() -> str:
            if ext in ("txt", "md"):
                return file_bytes.decode("utf-8", errors="replace")
            if ext == "pdf":
                import io

                from PyPDF2 import PdfReader

                reader = PdfReader(io.BytesIO(file_bytes))
                if len(reader.pages) > upload_max_pages:
                    raise HTTPException(
                        status_code=413,
                        detail="PDF exceeds the configured page limit",
                    )
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
            if ext == "docx":
                import io

                from docx import Document as DocxDocument

                doc = DocxDocument(io.BytesIO(file_bytes))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                return "\n\n".join(paragraphs)
            return ""

        try:
            content = await asyncio.to_thread(extract_text)
            if not content.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Could not extract any text from the uploaded file",
                )
            if len(content) > upload_max_text_chars:
                raise HTTPException(
                    status_code=413,
                    detail="Extracted text exceeds the configured limit",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"File parsing error: {e}")
            raise HTTPException(
                status_code=422,
                detail=f"Failed to parse file: {e}",
            ) from e

        final_title = title.strip() or filename.rsplit(".", 1)[0]

        result = await contribution_manager.submit(
            title=final_title,
            content=content,
            content_type="document",
            submitted_by=submitted_by,
            submitter_user_id=context.user_id,
            organization_id=context.organization_id,
            original_filename=filename,
            original_file_ext=ext,
        )

        try:
            original_path = (
                contribution_manager.contributions_dir / f"{result['id']}_original.{ext}"
            )
            await asyncio.to_thread(original_path.write_bytes, file_bytes)
            logger.info(f"Saved original file: {original_path.name}")
        except Exception as e:
            logger.warning(f"Could not save original file: {e}")

        return JSONResponse(
            content={
                "id": result["id"],
                "status": "pending",
                "message": f"Document uploaded ({len(content)} chars extracted)",
            }
        )

    @app.get(
        "/api/contributions/pending",
        response_model=ContributionListResponse,
    )
    async def get_pending_contributions(req: Request):
        """List all pending contributions (admin only)."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.REVIEW)
        pending = [
            item
            for item in await contribution_manager.list_pending()
            if item.get("organization_id") == context.organization_id
        ]
        return ContributionListResponse(contributions=pending, count=len(pending))

    @app.get("/api/contributions/count")
    async def get_contribution_count(req: Request):
        """Get count of pending contributions (for badge polling)."""
        if not contribution_manager:
            return {"count": 0}
        context = await require_context(req, Permission.REVIEW)
        pending = await contribution_manager.list_pending()
        return {
            "count": sum(item.get("organization_id") == context.organization_id for item in pending)
        }

    @app.get("/api/contributions/my")
    async def get_my_contributions(request: Request, submitted_by: str = ""):
        """Public endpoint — list all contributions for a given submitter name.

        Accepts the name via query param or ``grasp_user`` cookie.
        """
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(request, Permission.CONTRIBUTE)
        mine = await contribution_manager.list_for_user(context.user_id)
        return {"contributions": mine, "count": len(mine)}

    @app.get("/api/contributions/{contribution_id}/download")
    async def download_contribution_file(contribution_id: str, req: Request):
        """Download the original uploaded file for a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        contribution = await contribution_manager.get(contribution_id)
        if not contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        context = await require_context(req, Permission.CONTRIBUTE)
        if not policy.can_access_contribution(context, contribution.get("submitter_user_id")):
            raise HTTPException(status_code=403, detail="Access denied")
        ext = contribution.get("original_file_ext", "")
        if not ext:
            raise HTTPException(status_code=404, detail="No original file attached")
        original_path = contribution_manager.contributions_dir / f"{contribution_id}_original.{ext}"
        if not original_path.exists():
            raise HTTPException(status_code=404, detail="Original file not found on disk")
        original_filename = sanitize_filename(
            contribution.get("original_filename") or f"{contribution_id}.{ext}",
            max_length=150,
        )
        return FileResponse(
            path=str(original_path),
            filename=original_filename,
            media_type="application/octet-stream",
        )

    @app.get(
        "/api/contributions/{contribution_id}",
        response_model=ContributionResponse,
    )
    async def get_contribution(contribution_id: str, req: Request):
        """Get a single contribution by ID (admin only)."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.REVIEW)
        contribution = await contribution_manager.get(contribution_id)
        if not contribution or contribution.get("organization_id") != context.organization_id:
            raise HTTPException(status_code=404, detail="Contribution not found")
        return ContributionResponse(**contribution)

    @app.put(
        "/api/contributions/{contribution_id}",
        response_model=ContributionResponse,
    )
    async def update_contribution(
        contribution_id: str, request: ContributionUpdateRequest, req: Request
    ):
        """Admin edits the contribution content before approval."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.REVIEW)
        existing = await contribution_manager.get(contribution_id)
        if not existing or existing.get("organization_id") != context.organization_id:
            raise HTTPException(status_code=404, detail="Contribution not found")
        result = await contribution_manager.update_content(
            contribution_id,
            title=request.title,
            content=request.content,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Contribution not found or not pending")
        return ContributionResponse(**result)

    @app.post(
        "/api/contributions/{contribution_id}/approve",
        response_model=ContributionActionResponse,
    )
    async def approve_contribution(
        contribution_id: str, request: ContributionActionRequest, req: Request
    ):
        """Approve a contribution — classify and write to the repo."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.REVIEW)
        result = await contribution_manager.approve(
            contribution_id,
            reviewer=context,
            admin_notes=request.admin_notes,
        )
        if "error" in result:
            return ContributionActionResponse(status="error", message=result["error"])
        return ContributionActionResponse(**result)

    @app.post(
        "/api/contributions/{contribution_id}/reject",
        response_model=ContributionActionResponse,
    )
    async def reject_contribution(
        contribution_id: str, request: ContributionActionRequest, req: Request
    ):
        """Reject a contribution."""
        if not contribution_manager:
            raise HTTPException(status_code=503, detail="Contributions not available")
        context = await require_context(req, Permission.REVIEW)
        existing = await contribution_manager.get(contribution_id)
        if not existing or existing.get("organization_id") != context.organization_id:
            raise HTTPException(status_code=404, detail="Contribution not found")
        result = await contribution_manager.reject(
            contribution_id,
            admin_notes=request.admin_notes,
        )
        if "error" in result:
            return ContributionActionResponse(status="error", message=result["error"])
        return ContributionActionResponse(**result)

    # Structured memory

    def require_memory_service():
        if not memory_service:
            raise HTTPException(
                status_code=503,
                detail="Structured memory is not enabled",
            )
        return memory_service

    @app.get("/api/memory/status")
    async def memory_status():
        """Check whether structured memory is enabled."""
        return {"enabled": memory_service is not None}

    @app.get("/api/memory/entities", response_model=EntityListResponse)
    async def list_memory_entities(
        req: Request,
        query: str = "",
        entity_type: str = "",
        limit: int = 50,
    ):
        """Search or list entities in the structured memory."""
        context = await require_context(req, Permission.QUERY)
        svc = require_memory_service()
        if query:
            entities = await svc.search_entities(
                context, query, entity_type=entity_type or None, limit=limit
            )
        else:
            entities = await svc.find_entities(
                context, entity_type=entity_type or None, limit=limit
            )
        serialized = []
        for e in entities:
            e_copy = dict(e)
            for dt_key in ("valid_from", "valid_to"):
                val = e_copy.get(dt_key)
                if val is not None and not isinstance(val, str):
                    e_copy[dt_key] = val.isoformat()
            serialized.append(EntityResponse(**e_copy))
        return EntityListResponse(entities=serialized, count=len(serialized))

    @app.get("/api/memory/entities/{entity_id}", response_model=EntityDetailResponse)
    async def get_memory_entity(entity_id: str, req: Request):
        """Get an entity and its relationships."""
        context = await require_context(req, Permission.QUERY)
        svc = require_memory_service()
        entity = await svc.get_entity(context, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        for dt_key in ("valid_from", "valid_to"):
            val = entity.get(dt_key)
            if val is not None and not isinstance(val, str):
                entity[dt_key] = val.isoformat()
        rels = await svc.find_relationships(context, entity_id)
        rel_responses = []
        for r in rels:
            rel_responses.append(RelationshipResponse(
                id=r["id"],
                source_entity_id=r["source_entity_id"],
                relationship_type=r["relationship_type"],
                target_entity_id=r["target_entity_id"],
                evidence=r.get("evidence", []),
                confidence=r.get("confidence", "medium"),
            ))
        return EntityDetailResponse(
            entity=EntityResponse(**entity),
            relationships=rel_responses,
        )

    @app.post("/api/memory/entities/{entity_id}/review")
    async def review_memory_entity(
        entity_id: str, request: EntityReviewRequest, req: Request
    ):
        """Confirm, retire, or merge an entity."""
        context = await require_context(req, Permission.REVIEW)
        svc = require_memory_service()
        try:
            result = await svc.review_entity(
                context,
                entity_id,
                request.action,
                merge_target_id=request.merge_target_id,
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memory/work-items", response_model=WorkItemListResponse)
    async def list_memory_work_items(
        req: Request, status: str = "", limit: int = 50
    ):
        """List work items from structured memory."""
        context = await require_context(req, Permission.QUERY)
        svc = require_memory_service()
        items = await svc.list_work_items(
            context, status=status or None, limit=limit
        )
        serialized = []
        for item in items:
            i_copy = dict(item)
            for dt_key in ("due_at",):
                val = i_copy.get(dt_key)
                if val is not None and not isinstance(val, str):
                    i_copy[dt_key] = val.isoformat()
            serialized.append(WorkItemResponse(**i_copy))
        return WorkItemListResponse(work_items=serialized, count=len(serialized))

    @app.put("/api/memory/work-items/{item_id}/status")
    async def update_memory_work_item(
        item_id: str, request: WorkItemStatusRequest, req: Request
    ):
        """Transition a work item's status."""
        context = await require_context(req, Permission.REVIEW)
        svc = require_memory_service()
        try:
            return await svc.update_work_item_status(
                context, item_id, request.status
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memory/stats", response_model=MemoryStatsResponse)
    async def get_memory_stats(req: Request):
        """Get aggregate memory statistics."""
        context = await require_context(req, Permission.REVIEW)
        svc = require_memory_service()
        stats = await svc.get_memory_stats(context)
        return MemoryStatsResponse(**stats)

    @app.post("/api/memory/extract")
    async def extract_from_text(request: MemoryExtractRequest, req: Request):
        """Manually trigger entity extraction from provided text."""
        context = await require_context(req, Permission.REVIEW)
        svc = require_memory_service()
        evidence = {"source": "manual_extraction"}
        if request.source_label:
            evidence["label"] = request.source_label
        result = await svc.extract_entities_from_text(
            context, request.text, source_evidence=evidence
        )
        return result

    # Web pages

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
