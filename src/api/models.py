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
