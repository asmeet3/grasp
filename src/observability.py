"""Low-cardinality metrics and log redaction for baseline comparisons."""

from __future__ import annotations

import logging
import math
import re
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from .database import observability_snapshots_table

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|xoxb|ghp|ntn)_[A-Za-z0-9_-]{8,}\b"),
)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in SECRET_PATTERNS:
            message = pattern.sub("[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


@dataclass(frozen=True, slots=True)
class Distribution:
    count: int
    p50: float
    p95: float
    maximum: float


class MetricRecorder:
    """Thread-safe baseline recorder suitable for tests and a metrics adapter."""

    def __init__(self, max_samples: int = 10_000):
        self.max_samples = max_samples
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def observe(self, name: str, value: float) -> None:
        if not math.isfinite(value):
            return
        with self._lock:
            samples = self._samples[name]
            samples.append(value)
            if len(samples) > self.max_samples:
                del samples[: len(samples) - self.max_samples]

    def distribution(self, name: str) -> Distribution:
        with self._lock:
            ordered = sorted(self._samples.get(name, ()))
        if not ordered:
            return Distribution(0, 0.0, 0.0, 0.0)
        return Distribution(
            len(ordered),
            self._percentile(ordered, 0.50),
            self._percentile(ordered, 0.95),
            ordered[-1],
        )

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            names = tuple(self._samples)
        return {
            name: {
                "count": (distribution := self.distribution(name)).count,
                "p50": distribution.p50,
                "p95": distribution.p95,
                "maximum": distribution.maximum,
            }
            for name in names
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        index = max(0, min(len(values) - 1, math.ceil(len(values) * percentile) - 1))
        return values[index]


class MetricSessionStore:
    """Persist per-session metric snapshots so history survives restarts.

    The recorder keeps live low-latency samples in memory; this store writes
    cheap aggregate snapshots (count/p50/p95/max per metric) to PostgreSQL on
    a timer and at shutdown. One row per capture keeps hot paths free of
    per-sample database writes while providing previous-session history.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        session_id: str,
        started_at: datetime,
        host: str = "",
    ):
        self.engine = engine
        self.session_id = session_id
        self.started_at = started_at
        self.host = host

    async def save_snapshot(self, metrics: dict[str, dict[str, float | int]]) -> str:
        """Persist one aggregate snapshot for the current session."""
        snapshot_id = uuid.uuid4().hex
        async with self.engine.begin() as conn:
            await conn.execute(
                observability_snapshots_table.insert().values(
                    id=snapshot_id,
                    session_id=self.session_id,
                    started_at=self.started_at,
                    captured_at=datetime.now(UTC),
                    host=self.host,
                    metrics=metrics,
                )
            )
        return snapshot_id

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """Return the most recent capture for each session, newest first."""
        latest = (
            select(
                observability_snapshots_table.c.session_id,
                func.max(observability_snapshots_table.c.captured_at).label("captured_at"),
            )
            .group_by(observability_snapshots_table.c.session_id)
            .subquery()
        )
        statement = (
            select(observability_snapshots_table)
            .join(
                latest,
                and_(
                    observability_snapshots_table.c.session_id == latest.c.session_id,
                    observability_snapshots_table.c.captured_at == latest.c.captured_at,
                ),
            )
            .order_by(observability_snapshots_table.c.captured_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        async with self.engine.begin() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        return [dict(row) for row in rows]
