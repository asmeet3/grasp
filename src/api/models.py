"""Pydantic request/response models for all API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


# ── Query ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., description="The question to ask the institutional brain")


class QueryResponse(BaseModel):
    answer: str
    elapsed_seconds: float


# ── Sync ───────────────────────────────────────────────────

class SyncTriggerResponse(BaseModel):
    status: str
    message: str


class SyncStatusResponse(BaseModel):
    is_running: bool
    last_sync: dict | None = None
    next_scheduled: str | None = None
    workers: dict[str, dict] | None = None


# ── Changes ────────────────────────────────────────────────

class PendingChangesResponse(BaseModel):
    has_pending: bool
    changeset: dict | None = None


class ApproveRequest(BaseModel):
    message: str | None = Field(None, description="Optional custom commit message")


class ApproveResponse(BaseModel):
    status: str
    message: str | None = None
    push: str | None = None
    branch: str | None = None
    changes: dict | None = None
    error: str | None = None


class RejectResponse(BaseModel):
    status: str
    error: str | None = None


# ── Status ─────────────────────────────────────────────────

class SystemStatusResponse(BaseModel):
    status: str = "online"
    last_sync: dict | None = None
    next_scheduled: str | None = None
    connector_health: dict[str, bool] = {}
    document_stats: dict = {}
    vector_index: dict = {}


class SourcesResponse(BaseModel):
    sources: dict[str, Any] = {}


# ── Contributions ──────────────────────────────────────────

class ContributionSubmitRequest(BaseModel):
    title: str = Field(..., description="Title for the contribution")
    content: str = Field(..., description="The content to contribute")
    content_type: str = Field("document", description="Type: document, code, or plain_text")
    submitted_by: str = Field(..., description="Name of the submitter")


class ContributionSubmitResponse(BaseModel):
    id: str
    status: str
    message: str


class ContributionResponse(BaseModel):
    id: str
    title: str
    content: str
    content_type: str
    submitted_by: str
    submitted_at: str
    status: str
    admin_notes: str = ""
    resolved_at: str | None = None
    classified_as: str | None = None
    original_filename: str | None = None
    original_file_ext: str | None = None


class ContributionListResponse(BaseModel):
    contributions: list[ContributionResponse]
    count: int


class ContributionUpdateRequest(BaseModel):
    title: str | None = Field(None, description="Updated title")
    content: str | None = Field(None, description="Updated content")


class ContributionActionRequest(BaseModel):
    admin_notes: str = Field("", description="Optional admin notes")


class ContributionActionResponse(BaseModel):
    status: str
    message: str
    info_type: str | None = None
    error: str | None = None


# ── Authentication ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    first_name: str = Field(..., description="First name")
    last_name: str = Field(..., description="Last name")
    dob: str = Field(..., description="Date of birth (YYYY-MM-DD)")
    email: str = Field(..., description="Email address")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    confirm_password: str = Field(..., description="Password confirmation")


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google ID token from client-side sign-in")


class LoginRequest(BaseModel):
    email: str = Field(..., description="Email address")
    password: str = Field(..., description="Password")


class AuthResponse(BaseModel):
    token: str | None = None
    user: dict = {}
    pending: bool = False
    error: str | None = None
    conflict: str | None = None


class UserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    auth_method: str
    status: str
    role: str | None = None
    created_at: str
    approved_at: str | None = None


class UserListResponse(BaseModel):
    users: list[UserResponse]
    count: int


class ApproveUserRequest(BaseModel):
    role: str = Field(..., description="Role to assign: Intern, Associate, or Senior Associate")


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="New role: Intern, Associate, or Senior Associate")


# ── User Self-Service ──────────────────────────────────────

class UpdateProfileRequest(BaseModel):
    first_name: str | None = Field(None, description="Updated first name")
    last_name: str | None = Field(None, description="Updated last name")
    dob: str | None = Field(None, description="Updated date of birth (YYYY-MM-DD)")
    profile_picture: str | None = Field(None, description="Base64-encoded PNG data URL (256×256)")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=8, description="New password (min 8 characters)")
    confirm_new_password: str = Field(..., description="New password confirmation")


class DeleteAccountRequest(BaseModel):
    password: str | None = Field(None, description="Current password (required for email accounts, omit for Google)")

