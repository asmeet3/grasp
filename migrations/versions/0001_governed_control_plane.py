"""Governed control plane and revisioned knowledge.

Revision ID: 0001_governed_control_plane
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from src.database import metadata

revision = "0001_governed_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table) if fk["name"]}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "organizations" not in tables:
        op.create_table(
            "organizations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False, unique=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    op.execute(
        "INSERT INTO organizations (id, name) VALUES ('default', 'Default Organization') "
        "ON CONFLICT (id) DO NOTHING"
    )

    # A new installation has no legacy tables to alter.
    if "users" not in tables:
        metadata.create_all(op.get_bind(), checkfirst=True)
        return

    user_columns = _columns("users")
    if "organization_id" not in user_columns:
        op.add_column(
            "users",
            sa.Column("organization_id", sa.String(36), server_default="default", nullable=False),
        )
    if "job_title" not in user_columns:
        op.add_column("users", sa.Column("job_title", sa.Text(), nullable=True))
        op.execute("UPDATE users SET job_title = role WHERE job_title IS NULL")
    if "system_role" not in user_columns:
        op.add_column(
            "users",
            sa.Column("system_role", sa.String(32), server_default="member", nullable=False),
        )
    if "fk_users_organization" not in _foreign_keys("users"):
        op.create_foreign_key(
            "fk_users_organization",
            "users",
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")}
    if "uq_users_google_id" not in indexes:
        op.create_index("uq_users_google_id", "users", ["google_id"], unique=True)
    if "ix_users_org_status" not in indexes:
        op.create_index("ix_users_org_status", "users", ["organization_id", "status"])

    # Create new control-plane tables in dependency order from canonical metadata.
    metadata.create_all(op.get_bind(), checkfirst=True)

    contribution_columns = _columns("contributions")
    if "organization_id" not in contribution_columns:
        op.add_column(
            "contributions",
            sa.Column("organization_id", sa.String(36), server_default="default", nullable=False),
        )
    if "submitter_user_id" not in contribution_columns:
        op.add_column("contributions", sa.Column("submitter_user_id", sa.String(12), nullable=True))
        op.execute(
            "INSERT INTO users (id, organization_id, first_name, last_name, email, status, role, job_title, system_role) "
            "VALUES ('legacy000000', 'default', 'Legacy', 'Importer', 'legacy@local.invalid', 'approved', 'Associate', 'Associate', 'member') "
            "ON CONFLICT (id) DO NOTHING"
        )
        op.execute(
            "UPDATE contributions SET submitter_user_id = 'legacy000000' WHERE submitter_user_id IS NULL"
        )
        op.alter_column("contributions", "submitter_user_id", nullable=False)
    for name, column in (
        ("reviewer_user_id", sa.Column("reviewer_user_id", sa.String(12), nullable=True)),
        ("change_set_id", sa.Column("change_set_id", sa.String(36), nullable=True)),
    ):
        if name not in contribution_columns:
            op.add_column("contributions", column)
    contribution_fks = _foreign_keys("contributions")
    if "fk_contributions_organization" not in contribution_fks:
        op.create_foreign_key(
            "fk_contributions_organization",
            "contributions",
            "organizations",
            ["organization_id"],
            ["id"],
        )
    if "fk_contributions_submitter" not in contribution_fks:
        op.create_foreign_key(
            "fk_contributions_submitter",
            "contributions",
            "users",
            ["submitter_user_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if "fk_contributions_reviewer" not in contribution_fks:
        op.create_foreign_key(
            "fk_contributions_reviewer",
            "contributions",
            "users",
            ["reviewer_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "fk_contributions_changeset" not in contribution_fks:
        op.create_foreign_key(
            "fk_contributions_changeset",
            "contributions",
            "knowledge_changesets",
            ["change_set_id"],
            ["id"],
        )

    chat_fks = _foreign_keys("chat_threads")
    if "fk_chat_threads_user" not in chat_fks:
        op.create_foreign_key(
            "fk_chat_threads_user", "chat_threads", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    sync_columns = _columns("sync_state")
    if "started_at" not in sync_columns:
        op.add_column(
            "sync_state",
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    if "watermark" not in sync_columns:
        op.add_column(
            "sync_state",
            sa.Column(
                "watermark",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.execute("UPDATE sync_state SET watermark = timestamp")
    if "change_set_id" not in sync_columns:
        op.add_column("sync_state", sa.Column("change_set_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_sync_state_changeset",
            "sync_state",
            "knowledge_changesets",
            ["change_set_id"],
            ["id"],
        )


def downgrade() -> None:
    # Content and audit state are intentionally retained on downgrade. Rollback
    # is performed with feature flags and active-revision pointers.
    pass
