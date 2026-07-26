"""Low-cardinality metrics and log redaction for baseline comparisons."""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass

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
