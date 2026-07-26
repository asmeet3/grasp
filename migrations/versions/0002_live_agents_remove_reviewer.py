"""Activate governed agents and remove the standalone reviewer role.

Revision ID: 0002_live_agents_remove_reviewer
Revises: 0001_governed_control_plane
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_live_agents_remove_reviewer"
down_revision = "0001_governed_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET system_role = 'knowledge_editor' "
        "WHERE system_role = 'reviewer'"
    )
    inspector = sa.inspect(op.get_bind())
    if "agent_controls" not in set(inspector.get_table_names()):
        op.create_table(
            "agent_controls",
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "emergency_stopped",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "updated_by",
                sa.String(12),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    # Role assignments and run-safety state are deliberately retained.
    pass
