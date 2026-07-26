"""Relational schema for Grasp's governed control plane.

PostgreSQL is the system of record for identity, policy, review state, jobs,
audit history, and active revision pointers.  Git remains the source of truth
for knowledge content and Chroma is a rebuildable derived index.
"""

from __future__ import annotations

import logging

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

metadata = MetaData()


organizations_table = Table(
    "organizations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


users_table = Table(
    "users",
    metadata,
    Column("id", String(12), primary_key=True),
    Column(
        "organization_id",
        String(36),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        server_default="default",
    ),
    Column("first_name", Text, nullable=False, server_default=""),
    Column("last_name", Text, nullable=False, server_default=""),
    Column("dob", Text, nullable=False, server_default=""),
    Column("email", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False, server_default=""),
    Column("auth_method", String(10), nullable=False, server_default="email"),
    Column("status", String(20), nullable=False, server_default="pending_approval"),
    # ``role`` is retained as a read-compatible alias while clients migrate.
    Column("role", Text, nullable=True),
    Column("job_title", Text, nullable=True),
    Column("system_role", String(32), nullable=False, server_default="member"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("google_id", Text, nullable=True, unique=True),
    Column("profile_picture", Text, nullable=True),
    Column("password_version", Integer, nullable=False, server_default="0"),
    Index("ix_users_org_status", "organization_id", "status"),
    Index("ix_users_created_at", "created_at"),
)


knowledge_changesets_table = Table(
    "knowledge_changesets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("change_type", String(32), nullable=False),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column(
        "creator_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    Column("base_commit_sha", String(64), nullable=False),
    Column("state", String(32), nullable=False, server_default="draft"),
    Column("operations", JSONB, nullable=False, server_default="[]"),
    Column("provenance", JSONB, nullable=False, server_default="{}"),
    Column(
        "reviewer_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    Column("reviewed_at", DateTime(timezone=True), nullable=True),
    Column("review_explanation", Text, nullable=False, server_default=""),
    Column("final_commit_sha", String(64), nullable=True),
    Column("error", Text, nullable=False, server_default=""),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_changesets_org_state_created", "organization_id", "state", "created_at"),
    Index("ix_changesets_final_commit", "final_commit_sha"),
)


contributions_table = Table(
    "contributions",
    metadata,
    Column("id", String(12), primary_key=True),
    Column(
        "organization_id",
        String(36),
        ForeignKey("organizations.id"),
        nullable=False,
        server_default="default",
    ),
    Column("title", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_type", String(20), nullable=False, server_default="document"),
    Column("submitted_by", Text, nullable=False, server_default=""),
    Column(
        "submitter_user_id", String(12), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("submitted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("admin_notes", Text, nullable=False, server_default=""),
    Column(
        "reviewer_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("classified_as", Text, nullable=True),
    Column("original_filename", Text, nullable=True),
    Column("original_file_ext", Text, nullable=True),
    Column("change_set_id", String(36), ForeignKey("knowledge_changesets.id"), nullable=True),
    Index("ix_contributions_status_submitted", "status", "submitted_at"),
    Index("ix_contributions_submitter", "submitter_user_id", "submitted_at"),
)


chat_threads_table = Table(
    "chat_threads",
    metadata,
    Column("id", String(50), primary_key=True),
    Column("user_id", String(12), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("title", Text, nullable=False),
    Column("messages", JSONB, nullable=False, server_default="[]"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    Index("ix_chat_threads_user_updated", "user_id", "updated_at"),
)


sync_state_table = Table(
    "sync_state",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sync_type", String(20), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("watermark", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("total_docs", Integer, nullable=False, server_default="0"),
    Column("workers", JSONB, nullable=False, server_default="{}"),
    Column("details", JSONB, nullable=False, server_default="{}"),
    Column("change_set_id", String(36), ForeignKey("knowledge_changesets.id"), nullable=True),
    Index("ix_sync_state_timestamp", "timestamp"),
)


checkpoints_table = Table(
    "checkpoints",
    metadata,
    Column("connector", String(50), primary_key=True),
    Column("state", JSONB, nullable=False, server_default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


index_jobs_table = Table(
    "index_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "change_set_id",
        String(36),
        ForeignKey("knowledge_changesets.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("commit_sha", String(64), nullable=False),
    Column("state", String(20), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("expected_manifest", JSONB, nullable=False, server_default="{}"),
    Column("actual_manifest", JSONB, nullable=False, server_default="{}"),
    Column("error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("change_set_id", "commit_sha", name="uq_index_job_changeset_commit"),
    Index("ix_index_jobs_state_created", "state", "created_at"),
)


active_revisions_table = Table(
    "active_revisions",
    metadata,
    Column("organization_id", String(36), ForeignKey("organizations.id"), primary_key=True),
    Column("commit_sha", String(64), nullable=False),
    Column("index_name", Text, nullable=False),
    Column("embedding_model", Text, nullable=False),
    Column("index_schema_version", Integer, nullable=False),
    Column("manifest", JSONB, nullable=False, server_default="{}"),
    Column("activated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


jobs_table = Table(
    "jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_type", String(50), nullable=False),
    Column("payload", JSONB, nullable=False, server_default="{}"),
    Column("state", String(20), nullable=False, server_default="pending"),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("lease_owner", Text, nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("max_attempts", Integer, nullable=False, server_default="5"),
    Column("last_error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_jobs_claim", "state", "available_at", "lease_expires_at"),
)


dead_letters_table = Table(
    "dead_letters",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
    Column("job_type", String(50), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("error", Text, nullable=False),
    Column("failed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


audit_events_table = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_type", String(80), nullable=False),
    Column("actor_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=True),
    Column("resource_type", String(50), nullable=False),
    Column("resource_id", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_audit_resource_created", "resource_type", "resource_id", "created_at"),
    Index("ix_audit_org_created", "organization_id", "created_at"),
)


entities_table = Table(
    "entities",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column("entity_type", String(30), nullable=False),
    Column("canonical_name", Text, nullable=False),
    Column("aliases", JSONB, nullable=False, server_default="[]"),
    Column("deduplication_key", Text, nullable=False),
    Column("attributes", JSONB, nullable=False, server_default="{}"),
    Column("evidence", JSONB, nullable=False, server_default="[]"),
    Column("confidence", String(12), nullable=False, server_default="medium"),
    Column("sensitivity", String(20), nullable=False, server_default="internal"),
    Column("acl_principals", JSONB, nullable=False, server_default="[]"),
    Column("valid_from", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("valid_to", DateTime(timezone=True), nullable=True),
    UniqueConstraint("organization_id", "entity_type", "deduplication_key", name="uq_entity_dedup"),
)


entity_relationships_table = Table(
    "entity_relationships",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column(
        "source_entity_id",
        String(36),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_type", String(50), nullable=False),
    Column(
        "target_entity_id",
        String(36),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("evidence", JSONB, nullable=False, server_default="[]"),
    Column("confidence", String(12), nullable=False, server_default="medium"),
    Column("valid_from", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("valid_to", DateTime(timezone=True), nullable=True),
)


work_items_table = Table(
    "work_items",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("evidence", JSONB, nullable=False, server_default="[]"),
    Column("owner_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("due_at", DateTime(timezone=True), nullable=True),
    Column("confidence", String(12), nullable=False, server_default="low"),
    Column("status", String(20), nullable=False, server_default="proposed"),
    Column("deduplication_key", Text, nullable=False),
    Column("origin", JSONB, nullable=False, server_default="{}"),
    UniqueConstraint("organization_id", "deduplication_key", name="uq_work_item_dedup"),
)


skills_table = Table(
    "skills",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("commit_sha", String(64), nullable=False),
    Column("active", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("organization_id", "name", "version", name="uq_skill_version"),
)


actions_table = Table(
    "actions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column(
        "creator_user_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    Column("action_type", String(80), nullable=False),
    Column("state", String(20), nullable=False, server_default="planned"),
    Column("input", JSONB, nullable=False),
    Column("preview", JSONB, nullable=False, server_default="{}"),
    Column("result", JSONB, nullable=False, server_default="{}"),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("approval_required", Boolean, nullable=False, server_default="true"),
    Column("approved_by", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    Column("error", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


agent_definitions_table = Table(
    "agent_definitions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("definition", JSONB, nullable=False),
    Column("active", Boolean, nullable=False, server_default="false"),
    Column("failure_count", Integer, nullable=False, server_default="0"),
    Column("paused_reason", Text, nullable=False, server_default=""),
    UniqueConstraint("organization_id", "name", name="uq_agent_name"),
)


agent_runs_table = Table(
    "agent_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "agent_id",
        String(36),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("state", String(20), nullable=False, server_default="queued"),
    Column("lease_owner", Text, nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("input", JSONB, nullable=False, server_default="{}"),
    Column("output", JSONB, nullable=False, server_default="{}"),
    Column("cost_units", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


agent_controls_table = Table(
    "agent_controls",
    metadata,
    Column(
        "organization_id",
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("emergency_stopped", Boolean, nullable=False, server_default="false"),
    Column("reason", Text, nullable=False, server_default=""),
    Column(
        "updated_by",
        String(12),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


evaluation_runs_table = Table(
    "evaluation_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("organization_id", String(36), ForeignKey("organizations.id"), nullable=False),
    Column("suite", Text, nullable=False),
    Column("target_type", String(30), nullable=False),
    Column("target_version", Text, nullable=False),
    Column("metrics", JSONB, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine with a bounded connection pool."""
    return create_async_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )


async def init_db(engine: AsyncEngine) -> None:
    """Bootstrap a fresh database; deployed upgrades use Alembic migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            pg_insert(organizations_table)
            .values(id="default", name="Default Organization")
            .on_conflict_do_nothing(index_elements=["id"])
        )
        # Reviewer used to be a standalone access level. Knowledge editors
        # retain its useful review capability without audit/operations access.
        await conn.execute(
            users_table.update()
            .where(users_table.c.system_role == "reviewer")
            .values(system_role="knowledge_editor")
        )
    logger.info("Database tables verified or created")
