"""Pydantic request/response models for all API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Query


class ChatMessage(BaseModel):
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=20_000,
        description="The question to ask the institutional brain",
    )
    history: list[ChatMessage] | None = Field(
        None,
        description="Prior conversation messages for multi-turn chat context",
    )


# Sync


class SyncTriggerResponse(BaseModel):
    status: str
    message: str


class SyncStatusResponse(BaseModel):
    is_running: bool
    last_sync: dict | None = None
    next_scheduled: str | None = None
    workers: dict[str, dict] | None = None


# Changes


class PendingChangesResponse(BaseModel):
    has_pending: bool
    changeset: dict | None = None


class ApproveRequest(BaseModel):
    change_set_id: str | None = Field(
        None, description="Change set to approve; defaults to oldest pending"
    )
    message: str | None = Field(None, description="Optional custom commit message")
    explanation: str = Field("", description="Reviewer explanation")


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


class RejectRequest(BaseModel):
    change_set_id: str | None = None
    explanation: str = ""


# Status


class SystemStatusResponse(BaseModel):
    status: str = "online"
    last_sync: dict | None = None
    next_scheduled: str | None = None
    connector_health: dict[str, bool] = Field(default_factory=dict)
    document_stats: dict = Field(default_factory=dict)
    vector_index: dict = Field(default_factory=dict)


class SourcesResponse(BaseModel):
    sources: dict[str, Any] = Field(default_factory=dict)


# Contributions


class ContributionSubmitRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300, description="Title for the contribution")
    content: str = Field(
        ..., min_length=1, max_length=2_000_000, description="The content to contribute"
    )
    content_type: str = Field("document", description="Type: document, code, or plain_text")
    submitted_by: str = Field("", description="Deprecated; identity comes from the session")


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


# Chat threads


class SaveChatThreadRequest(BaseModel):
    id: str = Field(..., description="Unique chat ID")
    title: str = Field(..., description="Chat title")
    messages: list[dict] = Field(..., description="List of message objects (role, content)")
    created_at: str | None = Field(None, description="Optional ISO timestamp")


class ChatThreadResponse(BaseModel):
    id: str
    title: str
    messages: list[dict]
    created_at: str | None = None
    updated_at: str | None = None


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadResponse]


# Authentication


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
    user: dict = Field(default_factory=dict)
    pending: bool = False
    error: str | None = None
    conflict: str | None = None


class ApproveUserRequest(BaseModel):
    role: str = Field(..., description="Role to assign")
    system_role: str = Field("member", description="Security role to assign")


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="New role")
    system_role: str | None = Field(None, description="Optional new security role")


# Company-brain agents


class AgentRunRequest(BaseModel):
    prompt: str = Field("", max_length=4_000, description="Optional focus for this run")


class AgentActivationRequest(BaseModel):
    active: bool


class AgentEmergencyStopRequest(BaseModel):
    stopped: bool
    reason: str = Field("", max_length=500)


# User self-service


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
    password: str | None = Field(
        None, description="Current password (required for email accounts, omit for Google)"
    )


# Structured memory


class EntityResponse(BaseModel):
    id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Any] = Field(default_factory=list)
    confidence: str = "medium"
    sensitivity: str = "internal"
    valid_from: str | None = None
    valid_to: str | None = None


class EntityListResponse(BaseModel):
    entities: list[EntityResponse]
    count: int


class RelationshipResponse(BaseModel):
    id: str
    source_entity_id: str
    relationship_type: str
    target_entity_id: str
    evidence: list[Any] = Field(default_factory=list)
    confidence: str = "medium"


class EntityDetailResponse(BaseModel):
    entity: EntityResponse
    relationships: list[RelationshipResponse] = Field(default_factory=list)


class GraphResponse(BaseModel):
    nodes: list[EntityResponse]
    edges: list[RelationshipResponse]


class EntityReviewRequest(BaseModel):
    action: str = Field(..., description="Review action: 'confirm', 'retire', or 'merge'")
    merge_target_id: str | None = Field(
        None, description="Entity ID to merge into (required when action is 'merge')"
    )


class WorkItemResponse(BaseModel):
    id: str
    title: str
    evidence: list[Any] = Field(default_factory=list)
    owner_user_id: str | None = None
    due_at: str | None = None
    confidence: str = "low"
    status: str = "proposed"
    origin: dict[str, Any] = Field(default_factory=dict)


class WorkItemListResponse(BaseModel):
    work_items: list[WorkItemResponse]
    count: int


class WorkItemStatusRequest(BaseModel):
    status: str = Field(..., description="New status: 'accepted', 'completed', or 'dismissed'")


class MemoryStatsResponse(BaseModel):
    total_entities: int = 0
    entities_by_type: dict[str, int] = Field(default_factory=dict)
    total_relationships: int = 0
    work_items_by_status: dict[str, int] = Field(default_factory=dict)
    total_work_items: int = 0


class MemoryExtractRequest(BaseModel):
    text: str = Field(
        ..., min_length=10, max_length=50_000, description="Text to extract entities from"
    )
    source_label: str = Field(
        "", max_length=200, description="Optional label for the source of this text"
    )
