"""Two-phase, authorized, idempotent external action protocol."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .audit import PostgresAuditStore
from .core.security import AuthContext, Permission, PolicyEngine
from .database import actions_table

ActionHandler = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class ControlledActionService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        enabled: bool = False,
        audit: PostgresAuditStore | None = None,
    ):
        self.engine = engine
        self.enabled = enabled
        self.policy = PolicyEngine()
        self.audit = audit or PostgresAuditStore(engine)
        self.handlers: dict[str, ActionHandler] = {}

    def register(self, action_type: str, handler: ActionHandler) -> None:
        self.handlers[action_type] = handler

    async def plan(
        self,
        context: AuthContext,
        action_type: str,
        input_data: Mapping[str, Any],
        *,
        idempotency_key: str,
        approval_required: bool = True,
    ) -> str:
        self.policy.require(context, Permission.EXECUTE_ACTIONS)
        action_id = str(uuid.uuid4())
        stmt = (
            pg_insert(actions_table)
            .values(
                id=action_id,
                organization_id=context.organization_id,
                creator_user_id=context.user_id,
                action_type=action_type,
                input=dict(input_data),
                idempotency_key=idempotency_key,
                approval_required=approval_required,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(actions_table.c.id)
        )
        async with self.engine.begin() as conn:
            inserted = (await conn.execute(stmt)).scalar_one_or_none()
            if inserted:
                return str(inserted)
            return str(
                (
                    await conn.execute(
                        select(actions_table.c.id).where(
                            actions_table.c.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
            )

    async def preview(self, action_id: str, context: AuthContext) -> Mapping[str, Any]:
        row = await self._get_owned(action_id, context)
        preview = {"action_type": row["action_type"], "input": row["input"]}
        async with self.engine.begin() as conn:
            await conn.execute(
                update(actions_table)
                .where(actions_table.c.id == action_id)
                .values(state="previewed", preview=preview)
            )
        return preview

    async def approve(self, action_id: str, context: AuthContext) -> None:
        await self._get_owned(action_id, context)
        async with self.engine.begin() as conn:
            await conn.execute(
                update(actions_table)
                .where(actions_table.c.id == action_id)
                .values(state="approved", approved_by=context.user_id)
            )

    async def execute(self, action_id: str, context: AuthContext) -> Mapping[str, Any]:
        if not self.enabled:
            raise RuntimeError("ACTIONS_ENABLED is false")
        row = await self._get_owned(action_id, context)
        if row["state"] == "verified":
            return row["result"]
        if row["approval_required"] and row["state"] != "approved":
            raise PermissionError("Action requires approval")
        handler = self.handlers.get(row["action_type"])
        if not handler:
            raise ValueError("Action type is not allowlisted")
        result = dict(await handler(row["input"]))
        now = datetime.now(UTC)
        async with self.engine.begin() as conn:
            await conn.execute(
                update(actions_table)
                .where(actions_table.c.id == action_id)
                .values(state="verified", result=result, verified_at=now)
            )
        await self.audit.record(
            "action.verified",
            actor_id=context.user_id,
            organization_id=context.organization_id,
            resource_type="action",
            resource_id=action_id,
            details={"action_type": row["action_type"]},
        )
        return result

    async def _get_owned(self, action_id: str, context: AuthContext) -> dict[str, Any]:
        self.policy.require(context, Permission.EXECUTE_ACTIONS)
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(actions_table).where(
                            actions_table.c.id == action_id,
                            actions_table.c.organization_id == context.organization_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise ValueError("Action not found")
        return dict(row)
