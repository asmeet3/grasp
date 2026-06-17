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
