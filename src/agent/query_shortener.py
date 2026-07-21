"""Query shortener — LLM-based query decomposition for live platform search.

Takes a user's natural language query and breaks it into 1–3 concise,
search-friendly sub-queries suitable for platform APIs (Jira JQL text,
Confluence CQL, etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# Maximum number of shortened queries to produce.
# Keeps the fan-out manageable (N queries × M platforms).
MAX_SHORT_QUERIES = 3


class QueryShortener:
    """Shortens a user query into multiple concise search sub-queries via an LLM."""

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        system_prompt: str = "XYZ",
    ):
        self.client = AsyncAnthropic(api_key=anthropic_api_key)
        self.model = model
        self.system_prompt = system_prompt

    async def shorten(self, query: str) -> list[str]:
        """Break a user query into 1–3 concise search sub-queries.

        Returns a list of short keyword-style queries optimised for
        platform search APIs.  Falls back to [query] on any failure.
        """
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=self.system_prompt,
                temperature=0.1,
                messages=[
                    {
                        "role": "user",
                        "content": query,
                    }
                ],
            )

            raw_text = response.content[0].text.strip()
            short_queries = self._parse_response(raw_text)

            if short_queries:
                logger.info(
                    f"Query shortened into {len(short_queries)} sub-queries: "
                    f"{short_queries}"
                )
                return short_queries[:MAX_SHORT_QUERIES]

            # Parsing failed — fall back
            logger.warning(
                "Query shortener returned unparseable output, "
                "falling back to original query"
            )
            return [query]

        except Exception as e:
            logger.error(f"Query shortening failed: {e} — using original query")
            return [query]

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _parse_response(text: str) -> list[str] | None:
        """Try to extract a JSON list of strings from the LLM response."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (with optional language tag) and closing fence
            lines = cleaned.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

        if isinstance(parsed, list) and all(isinstance(q, str) for q in parsed):
            # Filter out empties and enforce max
            return [q.strip() for q in parsed if q.strip()][:MAX_SHORT_QUERIES]

        return None
