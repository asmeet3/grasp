"""Coordinator agent — Claude-powered agentic query engine.

Implements the three-phase query architecture:
1. Parallel fan-out to all sources via sub-agents
2. Coordinator synthesis using Claude with tool-use
3. Optional deep-dive follow-ups

Streams responses via an async generator for SSE support.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Any

from anthropic import AsyncAnthropic

from .sub_agents import SubAgent, SubAgentDispatcher
from .tools import TOOL_DEFINITIONS, ToolExecutor
from ..connectors.base import BaseConnector, Document
from ..index.vector_store import VectorStore, SearchResult
from ..repo.manager import RepoManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are **Grasp**, an expert AI assistant that serves as a company's institutional brain. You have deep knowledge of the organization's technical architecture, ongoing projects, operational processes, and strategic direction.

## Your Capabilities
You have access to tools that let you search both a comprehensive knowledge repository (containing historical data from Confluence, Jira, SharePoint, Slack, and Notion) and live platform APIs for the most recent information.

## How to Answer Questions

1. **Start with fan_out_search**: For any new question, ALWAYS begin by calling `fan_out_search` with a well-crafted query. This searches ALL sources simultaneously in parallel — it's the fastest way to gather broad context.

2. **Analyze the results**: Review what came back from all sources. Identify the most relevant information.

3. **Deep-dive if needed**: If the fan-out results aren't sufficient, use targeted tools:
   - `read_repo_file` to get the full content of a relevant document
   - `search_knowledge_repo` with specific filters (source, info_type) for targeted vector search
   - Individual platform search tools for focused follow-ups

4. **Synthesize your answer**: Combine information from multiple sources into a comprehensive, well-structured response.

## Response Guidelines
- **Cite your sources**: Always mention where information came from (e.g., "According to a Confluence page on API Design..." or "A recent Slack discussion in #engineering mentioned..."). Include URLs when available.
- **Be comprehensive but concise**: Cover all relevant aspects without unnecessary padding.
- **Distinguish between historical and live data**: If information comes from the cached repository vs. a live query, note the freshness.
- **Acknowledge uncertainty**: If you can't find sufficient information, say so clearly rather than guessing.
- **Use structured formatting**: Use headings, bullet points, and bold text to make answers scannable.
"""


class QueryEngine:
    """The coordinator agent that orchestrates query answering."""

    MAX_ROUNDS = 4  # Max follow-up rounds after initial fan-out

    def __init__(
        self,
        anthropic_api_key: str,
        model: str,
        tool_executor: ToolExecutor,
    ):
        self.client = AsyncAnthropic(api_key=anthropic_api_key)
        self.model = model
        self.tool_executor = tool_executor

    async def query(self, question: str) -> str:
        """Execute a query and return the complete answer."""
        result_parts = []
        async for chunk in self.query_stream(question):
            result_parts.append(chunk)
        return "".join(result_parts)

    async def query_stream(self, question: str) -> AsyncGenerator[str, None]:
        """Execute a query with streaming response.

        Implements the three-phase query architecture:
        1. Auto-trigger fan_out_search
        2. Claude synthesizes from gathered context
        3. Optional follow-up rounds for deep-dives
        """
        start_time = time.time()

        # Phase 1: Auto fan-out search
        logger.info(f"Query received: '{question[:100]}...'")
        fan_out_context = await self.tool_executor.execute("fan_out_search", {"query": question})

        # Build initial messages with fan-out results pre-loaded
        messages = [
            {
                "role": "user",
                "content": question,
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "auto_fan_out",
                        "name": "fan_out_search",
                        "input": {"query": question},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "auto_fan_out",
                        "content": fan_out_context,
                    }
                ],
            },
        ]

        # Phase 2 & 3: Claude synthesis + optional follow-ups
        for round_num in range(self.MAX_ROUNDS + 1):
            logger.info(f"Agent round {round_num + 1}")

            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"Claude API error: {e}")
                yield f"\n\n*Error communicating with AI: {e}*"
                return

            # Process the response
            has_tool_use = False
            assistant_content = []
            tool_results = []

            for block in response.content:
                assistant_content.append(block)

                if block.type == "text":
                    yield block.text

                elif block.type == "tool_use":
                    has_tool_use = True
                    logger.info(f"Tool call: {block.name}({json.dumps(block.input)[:200]})")

                    # Execute the tool
                    result = await self.tool_executor.execute(block.name, block.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # If Claude made tool calls, continue the loop
            if has_tool_use and response.stop_reason == "tool_use":
                # Add assistant turn with tool_use blocks
                messages.append({
                    "role": "assistant",
                    "content": [
                        {"type": b.type, "id": b.id, "name": b.name, "input": b.input}
                        if b.type == "tool_use"
                        else {"type": "text", "text": b.text}
                        for b in assistant_content
                    ],
                })

                # Add tool results
                messages.append({
                    "role": "user",
                    "content": tool_results,
                })

                continue

            # Claude finished (stop_reason == "end_turn")
            break

        elapsed = time.time() - start_time
        logger.info(f"Query completed in {elapsed:.1f}s")
