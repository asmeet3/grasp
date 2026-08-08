"""Append-only audit event persistence and querying."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
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

    async def query(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        actor_user_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return audit events for an organization, newest first, with a total count.

        Filters are optional; ``resource_id`` matches a substring, while every
        other filter uses exact equality.  Events are always scoped to the
        requesting organization.
        """
        conditions = [audit_events_table.c.organization_id == organization_id]
        if event_type:
            conditions.append(audit_events_table.c.event_type == event_type)
        if actor_user_id:
            conditions.append(audit_events_table.c.actor_user_id == actor_user_id)
        if resource_type:
            conditions.append(audit_events_table.c.resource_type == resource_type)
        if resource_id:
            conditions.append(audit_events_table.c.resource_id.ilike(f"%{resource_id}%"))
        if start:
            conditions.append(audit_events_table.c.created_at >= start)
        if end:
            conditions.append(audit_events_table.c.created_at <= end)

        async with self.engine.connect() as conn:
            total = (
                await conn.scalar(
                    select(func.count())
                    .select_from(audit_events_table)
                    .where(*conditions)
                )
                or 0
            )
            rows = (
                await conn.execute(
                    select(audit_events_table)
                    .where(*conditions)
                    .order_by(
                        audit_events_table.c.created_at.desc(),
                        audit_events_table.c.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).mappings().all()
        return [dict(row) for row in rows], int(total)

    async def summary(
        self,
        *,
        organization_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return aggregate audit statistics for a recent window."""
        since = datetime.now(UTC) - timedelta(days=days)
        conditions = [
            audit_events_table.c.organization_id == organization_id,
            audit_events_table.c.created_at >= since,
        ]
        async with self.engine.connect() as conn:
            total = (
                await conn.scalar(
                    select(func.count())
                    .select_from(audit_events_table)
                    .where(*conditions)
                )
                or 0
            )
            by_event_type = {
                row.event_type: int(row.count)
                for row in await conn.execute(
                    select(
                        audit_events_table.c.event_type,
                        func.count().label("count"),
                    )
                    .where(*conditions)
                    .group_by(audit_events_table.c.event_type)
                    .order_by(func.count().desc())
                )
            }
            day_expr = func.date(audit_events_table.c.created_at)
            by_day = [
                {"date": row.day.isoformat(), "count": int(row.count)}
                for row in await conn.execute(
                    select(
                        day_expr.label("day"),
                        func.count().label("count"),
                    )
                    .where(*conditions)
                    .group_by(day_expr)
                    .order_by(day_expr)
                )
            ]
        return {
            "total": int(total),
            "days": days,
            "since": since.isoformat(),
            "by_event_type": by_event_type,
            "by_day": by_day,
        }
