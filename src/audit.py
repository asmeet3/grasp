"""Append-only audit event persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from .database import audit_events_table


class PostgresAuditStore:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def record(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        organization_id: str | None,
        resource_type: str,
        resource_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                audit_events_table.insert().values(
                    event_type=event_type,
                    actor_user_id=actor_id,
                    organization_id=organization_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details=dict(details or {}),
                )
            )
