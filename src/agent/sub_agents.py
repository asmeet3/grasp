"""Sub-agent definitions and parallel dispatcher.

Each sub-agent wraps a search function with timeout, error boundary,
and structured result formatting. The dispatcher fans out all sub-agents
concurrently via asyncio.gather.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from ..connectors.base import Document

logger = logging.getLogger(__name__)


@dataclass
class SubAgentResult:
    """Structured result from a single sub-agent."""
    source: str
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: float = 0.0
    timed_out: bool = False

    def to_context_string(self) -> str:
        """Format this result as a string for the coordinator agent."""
        if self.error:
            return f"[{self.source.upper()}] Error: {self.error}"
        if not self.results:
            return f"[{self.source.upper()}] No results found."

        lines = [f"[{self.source.upper()}] Found {len(self.results)} results ({self.elapsed_ms:.0f}ms):"]
        for i, r in enumerate(self.results, 1):
            title = r.get("title", "Untitled")
            snippet = r.get("snippet", "")[:300]
            url = r.get("url", "")
            lines.append(f"  {i}. **{title}**")
            if url:
                lines.append(f"     URL: {url}")
            if snippet:
                lines.append(f"     {snippet}")
            lines.append("")

        return "\n".join(lines)


class SubAgent:
    """A single sub-agent that wraps a search function with timeout and error handling."""

    def __init__(
        self,
        name: str,
        source: str,
        search_fn: Callable[[str], Awaitable[list[Document]]],
        timeout: float = 10.0,
    ):
        self.name = name
        self.source = source
        self.search_fn = search_fn
        self.timeout = timeout

    async def execute(self, query: str) -> SubAgentResult:
        """Execute the search with timeout and error boundary."""
        start = time.time()
        try:
            results = await asyncio.wait_for(
                self.search_fn(query),
                timeout=self.timeout,
            )

            elapsed_ms = (time.time() - start) * 1000

            formatted = []
            for doc in results:
                formatted.append({
                    "title": doc.title,
                    "snippet": doc.content[:500] if doc.content else "",
                    "url": doc.url,
                    "source": doc.source,
                    "doc_id": doc.id,
                    "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
                })

            return SubAgentResult(
                source=self.source,
                results=formatted,
                elapsed_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.time() - start) * 1000
            logger.warning(f"Sub-agent {self.name} timed out after {self.timeout}s")
            return SubAgentResult(
                source=self.source,
                error=f"Timed out after {self.timeout}s",
                elapsed_ms=elapsed_ms,
                timed_out=True,
            )

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(f"Sub-agent {self.name} failed: {e}")
            return SubAgentResult(
                source=self.source,
                error=str(e),
                elapsed_ms=elapsed_ms,
            )


class SubAgentDispatcher:
    """Dispatches multiple sub-agents in parallel and aggregates results."""

    def __init__(self):
        self.sub_agents: list[SubAgent] = []

    def register(self, agent: SubAgent):
        """Register a sub-agent for parallel dispatch."""
        self.sub_agents.append(agent)

    async def fan_out(self, query: str) -> list[SubAgentResult]:
        """Execute all sub-agents in parallel and return aggregated results."""
        if not self.sub_agents:
            return []

        logger.info(f"Dispatching {len(self.sub_agents)} sub-agents for query: '{query[:80]}...'")
        start = time.time()

        tasks = [agent.execute(query) for agent in self.sub_agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to SubAgentResult
        final_results: list[SubAgentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                agent = self.sub_agents[i]
                final_results.append(SubAgentResult(
                    source=agent.source,
                    error=str(result),
                ))
            else:
                final_results.append(result)

        total_ms = (time.time() - start) * 1000
        total_results = sum(len(r.results) for r in final_results)
        errors = sum(1 for r in final_results if r.error)

        logger.info(
            f"Fan-out complete: {total_results} results from "
            f"{len(final_results) - errors}/{len(final_results)} agents "
            f"in {total_ms:.0f}ms"
        )

        return final_results

    def format_all_results(self, results: list[SubAgentResult]) -> str:
        """Format all sub-agent results into a single context string."""
        parts = []
        for result in results:
            context = result.to_context_string()
            if context:
                parts.append(context)

        return "\n\n".join(parts)
