"""Declarative role-agent definitions and guarded routine leases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from .core.security import AuthContext, Permission, PolicyEngine
from .database import agent_definitions_table, agent_runs_table


class AgentDefinition(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role: str
    owner_user_id: str
    domains: list[str]
    skills: list[str]
    allowed_classifications: list[str]
    allowed_actions: list[str]
    approval_thresholds: dict[str, Any]
    schedule: str | None = None
    event_triggers: list[str] = Field(default_factory=list)
    runtime_budget_seconds: int = Field(gt=0, le=3600)
    cost_budget_units: int = Field(ge=0)
    concurrency_limit: int = Field(ge=1, le=20)
    escalation_path: str


class AgentService:
    def __init__(self, engine: AsyncEngine, *, enabled: bool = False):
        self.engine = engine
        self.enabled = enabled
        self.emergency_stop = False
        self.policy = PolicyEngine()

    async def register(self, context: AuthContext, definition: AgentDefinition) -> str:
        self.policy.require(context, Permission.MANAGE_AGENTS)
        agent_id = str(uuid.uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(
                agent_definitions_table.insert().values(
                    id=agent_id,
                    organization_id=context.organization_id,
                    name=definition.name,
                    definition=definition.model_dump(),
                    active=False,
                )
            )
        return agent_id

    async def lease_run(
        self,
        agent_id: str,
        input_data: dict[str, Any],
        *,
        lease_owner: str,
        lease_seconds: int = 300,
    ) -> str:
        if not self.enabled or self.emergency_stop:
            raise RuntimeError("Agent execution is disabled")
        now = datetime.now(UTC)
        async with self.engine.begin() as conn:
            agent = (
                (
                    await conn.execute(
                        select(agent_definitions_table).where(
                            agent_definitions_table.c.id == agent_id,
                            agent_definitions_table.c.active.is_(True),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not agent:
                raise ValueError("Active agent not found")
            definition = agent["definition"]
            active_runs = (
                await conn.execute(
                    select(agent_runs_table.c.id).where(
                        agent_runs_table.c.agent_id == agent_id,
                        agent_runs_table.c.state.in_(("queued", "running")),
                    )
                )
            ).all()
            if len(active_runs) >= int(definition["concurrency_limit"]):
                raise RuntimeError("Agent concurrency limit reached")
            run_id = str(uuid.uuid4())
            await conn.execute(
                agent_runs_table.insert().values(
                    id=run_id,
                    agent_id=agent_id,
                    state="running",
                    lease_owner=lease_owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    input=input_data,
                )
            )
            return run_id

    async def record_failure(self, agent_id: str, *, pause_after: int = 3) -> None:
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    select(agent_definitions_table.c.failure_count).where(
                        agent_definitions_table.c.id == agent_id
                    )
                )
            ).first()
            if not row:
                return
            count = int(row[0]) + 1
            values: dict[str, Any] = {"failure_count": count}
            if count >= pause_after:
                values.update(active=False, paused_reason="Repeated execution failures")
            await conn.execute(
                update(agent_definitions_table)
                .where(agent_definitions_table.c.id == agent_id)
                .values(**values)
            )

    def stop_all(self) -> None:
        self.emergency_stop = True
