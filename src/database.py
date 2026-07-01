"""Database layer — async PostgreSQL via SQLAlchemy.

Defines all table schemas and provides engine initialization.
Tables are created automatically on first startup via ``init_db()``.
"""

from __future__ import annotations

import logging

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    DateTime,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

metadata = MetaData()

# ── Users ──────────────────────────────────────────────────

users_table = Table(
    "users",
    metadata,
    Column("id", String(12), primary_key=True),
    Column("first_name", Text, nullable=False, server_default=""),
    Column("last_name", Text, nullable=False, server_default=""),
    Column("dob", Text, nullable=False, server_default=""),
    Column("email", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False, server_default=""),
    Column("auth_method", String(10), nullable=False, server_default="email"),
    Column("status", String(20), nullable=False, server_default="pending_approval"),
    Column("role", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("google_id", Text, nullable=True),
    Column("profile_picture", Text, nullable=True),
    Column("password_version", Integer, nullable=False, server_default="0"),
)

# ── Contributions ──────────────────────────────────────────

contributions_table = Table(
    "contributions",
    metadata,
    Column("id", String(12), primary_key=True),
    Column("title", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_type", String(20), nullable=False, server_default="document"),
    Column("submitted_by", Text, nullable=False, server_default=""),
    Column("submitted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("admin_notes", Text, nullable=False, server_default=""),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("classified_as", Text, nullable=True),
    Column("original_filename", Text, nullable=True),
    Column("original_file_ext", Text, nullable=True),
)

# ── Sync State ─────────────────────────────────────────────

sync_state_table = Table(
    "sync_state",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sync_type", String(20), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("total_docs", Integer, nullable=False, server_default="0"),
    Column("workers", JSONB, nullable=False, server_default="{}"),
    Column("details", JSONB, nullable=False, server_default="{}"),
)

# ── Checkpoints ────────────────────────────────────────────

checkpoints_table = Table(
    "checkpoints",
    metadata,
    Column("connector", String(50), primary_key=True),
    Column("state", JSONB, nullable=False, server_default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


# ── Engine & Initialization ────────────────────────────────

def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine with a connection pool."""
    return create_async_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables if they don't already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    logger.info("✓ Database tables verified / created")
