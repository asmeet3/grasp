"""Base connector interface and shared data models.

All platform connectors inherit from BaseConnector and implement
the three retrieval modes: full, incremental, and live search.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import AsyncGenerator, Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Document:
    """A single piece of retrieved content from any platform."""

    id: str
    source: str                    # confluence | jira | sharepoint | slack | notion
    title: str
    content: str                   # Markdown-formatted body text
    url: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Document:
        data = dict(data)
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


class RateLimiter:
    """Simple async rate limiter with exponential backoff on 429s."""

    def __init__(self, max_retries: int = 5, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._lock = asyncio.Lock()

    async def execute(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Execute an HTTP request with automatic retry on rate limits."""
        for attempt in range(self.max_retries + 1):
            async with self._lock:
                pass  # Serialize requests to avoid burst
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self.base_delay * (2 ** attempt)))
                    logger.warning(f"Rate limited on {url}, retrying in {retry_after:.1f}s (attempt {attempt + 1})")
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    retry_after = float(e.response.headers.get("Retry-After", self.base_delay * (2 ** attempt)))
                    logger.warning(f"Rate limited on {url}, retrying in {retry_after:.1f}s (attempt {attempt + 1})")
                    await asyncio.sleep(retry_after)
                    continue
                raise
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"Connection error on {url}: {e}, retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                raise
        raise RuntimeError(f"Max retries exceeded for {url}")


class BaseConnector(ABC):
    """Abstract base for all platform connectors.

    Every connector must implement three retrieval modes:
    - full_retrieve:        Stream all content (used for initial sync)
    - incremental_retrieve: Stream only new/changed content since a timestamp
    - live_search:          Real-time search for recent content (query-time)
    """

    def __init__(self, name: str):
        self.name = name
        self.rate_limiter = RateLimiter()
        self.logger = logging.getLogger(f"grasp.connector.{name}")

    @abstractmethod
    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Yield batches of documents for a full sync.

        If a checkpoint is provided, resume from that state.
        Each yielded batch should also return checkpoint state via the
        `get_checkpoint_state()` method.
        """
        ...

    @abstractmethod
    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Yield batches of documents changed since the given timestamp."""
        ...

    @abstractmethod
    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search the platform for recent content matching the query.

        Used at query-time to supplement the cached knowledge repo.
        """
        ...

    @abstractmethod
    def get_checkpoint_state(self) -> dict:
        """Return current state for checkpointing.

        Called after each batch during full_retrieve to enable resume.
        """
        ...

    def checkpoint_key(self) -> str:
        """Unique key for this connector's checkpoint file."""
        return self.name

    async def health_check(self) -> bool:
        """Check if this connector can reach its platform. Override if needed."""
        return True


def html_to_markdown(html: str) -> str:
    """Convert HTML content to clean Markdown."""
    from markdownify import markdownify
    from bs4 import BeautifulSoup

    if not html or not html.strip():
        return ""

    # Clean up the HTML first
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style tags
    for tag in soup(["script", "style"]):
        tag.decompose()

    md = markdownify(str(soup), heading_style="ATX", strip=["img"])
    # Clean up excessive whitespace
    lines = [line.rstrip() for line in md.splitlines()]
    # Remove more than 2 consecutive blank lines
    cleaned = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Convert a title into a safe filename."""
    import re
    # Replace problematic characters
    safe = re.sub(r'[<>:"/\\|?*]', '_', name)
    safe = re.sub(r'\s+', '_', safe)
    safe = re.sub(r'_+', '_', safe)
    safe = safe.strip('_. ')
    # Truncate
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip('_')
    return safe or "untitled"
