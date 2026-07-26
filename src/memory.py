"""Typed, ACL-governed organizational memory access."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .core.security import AuthContext, PolicyEngine
from .database import entities_table, entity_relationships_table, work_items_table


class StructuredMemoryService:
    def __init__(self, engine: AsyncEngine, policy: PolicyEngine | None = None):
        self.engine = engine
        self.policy = policy or PolicyEngine()

    async def find_entities(
        self, context: AuthContext, *, entity_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        stmt = select(entities_table).where(
            entities_table.c.organization_id == context.organization_id,
            entities_table.c.valid_to.is_(None),
        )
        if entity_type:
            stmt = stmt.where(entities_table.c.entity_type == entity_type)
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt.limit(min(limit, 100)))).mappings().all()
        return [
            dict(row)
            for row in rows
            if self.policy.can_access_principals(context, row["acl_principals"])
        ]

    async def upsert_entity(self, context: AuthContext, values: dict[str, Any]) -> str:
        """Typed upsert only; arbitrary SQL is never accepted."""
        entity_id = str(values.get("id") or uuid.uuid4())
        allowed = {
            "entity_type",
            "canonical_name",
            "aliases",
            "deduplication_key",
            "attributes",
            "evidence",
            "confidence",
            "sensitivity",
            "acl_principals",
            "valid_from",
            "valid_to",
        }
        data = {key: value for key, value in values.items() if key in allowed}
        data.update(id=entity_id, organization_id=context.organization_id)
        if not data.get("acl_principals"):
            data["acl_principals"] = [f"organization:{context.organization_id}"]
        stmt = (
            pg_insert(entities_table)
            .values(**data)
            .on_conflict_do_update(
                constraint="uq_entity_dedup",
                set_={
                    key: value
                    for key, value in data.items()
                    if key not in {"id", "organization_id"}
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return entity_id

    async def add_relationship(self, context: AuthContext, values: dict[str, Any]) -> str:
        relationship_id = str(uuid.uuid4())
        allowed = {
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "evidence",
            "confidence",
            "valid_from",
            "valid_to",
        }
        data = {key: value for key, value in values.items() if key in allowed}
        data.update(id=relationship_id, organization_id=context.organization_id)
        async with self.engine.begin() as conn:
            await conn.execute(entity_relationships_table.insert().values(**data))
        return relationship_id

    async def propose_work_item(self, context: AuthContext, values: dict[str, Any]) -> str:
        item_id = str(uuid.uuid4())
        data = {
            "id": item_id,
            "organization_id": context.organization_id,
            "title": values["title"],
            "evidence": values.get("evidence", []),
            "owner_user_id": values.get("owner_user_id"),
            "due_at": values.get("due_at"),
            "confidence": values.get("confidence", "low"),
            "status": "proposed",
            "deduplication_key": values["deduplication_key"],
            "origin": values.get("origin", {"user_id": context.user_id}),
        }
        async with self.engine.begin() as conn:
            await conn.execute(
                pg_insert(work_items_table)
                .values(**data)
                .on_conflict_do_nothing(constraint="uq_work_item_dedup")
            )
        return item_id
