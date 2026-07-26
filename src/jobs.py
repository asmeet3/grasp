"""PostgreSQL-backed idempotent job queue with leases and dead letters."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .database import dead_letters_table, jobs_table

logger = logging.getLogger(__name__)
JobHandler = Callable[[Mapping[str, Any]], Awaitable[None]]


class PostgresJobQueue:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        poll_seconds: float = 1.0,
        lease_seconds: int = 300,
        concurrency: int = 4,
        worker_id: str | None = None,
    ):
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.concurrency = max(1, min(concurrency, 32))
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self.handlers: dict[str, JobHandler] = {}
        self._stopping = asyncio.Event()

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type in self.handlers:
            raise ValueError(f"Handler already registered for {job_type}")
        self.handlers[job_type] = handler

    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        available_at: datetime | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        stmt = (
            pg_insert(jobs_table)
            .values(
                id=job_id,
                job_type=job_type,
                payload=dict(payload),
                idempotency_key=idempotency_key,
                available_at=available_at or datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(jobs_table.c.id)
        )
        async with self.engine.begin() as conn:
            inserted = (await conn.execute(stmt)).scalar_one_or_none()
            if inserted:
                return inserted
            existing = await conn.execute(
                select(jobs_table.c.id).where(jobs_table.c.idempotency_key == idempotency_key)
            )
            return str(existing.scalar_one())

    async def claim(self) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=self.lease_seconds)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                select(jobs_table)
                .where(
                    jobs_table.c.available_at <= now,
                    or_(
                        jobs_table.c.state == "pending",
                        ((jobs_table.c.state == "running") & (jobs_table.c.lease_expires_at < now)),
                    ),
                )
                .order_by(jobs_table.c.available_at.asc(), jobs_table.c.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = result.mappings().first()
            if not row:
                return None
            await conn.execute(
                update(jobs_table)
                .where(jobs_table.c.id == row["id"])
                .values(
                    state="running",
                    lease_owner=self.worker_id,
                    lease_expires_at=lease_until,
                    attempts=jobs_table.c.attempts + 1,
                    updated_at=now,
                )
            )
            claimed = dict(row)
            claimed["attempts"] = int(row["attempts"]) + 1
            return claimed

    async def run_once(self) -> bool:
        job = await self.claim()
        if not job:
            return False
        handler = self.handlers.get(job["job_type"])
        if not handler:
            await self._fail(job, f"No handler registered for {job['job_type']}")
            return True
        try:
            handler_payload = dict(job["payload"])
            handler_payload.setdefault("_job_id", job["id"])
            await handler(handler_payload)
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(jobs_table)
                    .where(
                        jobs_table.c.id == job["id"],
                        jobs_table.c.lease_owner == self.worker_id,
                    )
                    .values(
                        state="completed",
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
            return True
        except Exception as exc:
            logger.exception("Job %s failed", job["id"])
            await self._fail(job, str(exc))
            return True

    async def _fail(self, job: Mapping[str, Any], error: str) -> None:
        now = datetime.now(UTC)
        attempts = int(job.get("attempts", 1))
        max_attempts = int(job.get("max_attempts", 5))
        async with self.engine.begin() as conn:
            if attempts >= max_attempts:
                await conn.execute(
                    update(jobs_table)
                    .where(jobs_table.c.id == job["id"])
                    .values(
                        state="dead",
                        last_error=error,
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )
                await conn.execute(
                    dead_letters_table.insert().values(
                        id=str(uuid.uuid4()),
                        job_id=job["id"],
                        job_type=job["job_type"],
                        payload=dict(job["payload"]),
                        error=error,
                    )
                )
            else:
                delay = min(300, 2**attempts)
                await conn.execute(
                    update(jobs_table)
                    .where(jobs_table.c.id == job["id"])
                    .values(
                        state="pending",
                        available_at=now + timedelta(seconds=delay),
                        last_error=error,
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )

    async def run_forever(self) -> None:
        """Run a bounded set of workers against the shared durable queue."""
        self._stopping.clear()
        workers = [
            asyncio.create_task(self._worker_loop(index)) for index in range(self.concurrency)
        ]
        await asyncio.gather(*workers)

    async def _worker_loop(self, _index: int) -> None:
        while not self._stopping.is_set():
            worked = await self.run_once()
            if not worked:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopping.set()
