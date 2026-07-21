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

1. **Start with fan_out_search**: For any new question, ALWAYS begin by calling `fan_out_search` with a well-crafted query. This performs a two-branch parallel search:
   - **Branch 1**: Searches the ChromaDB knowledge repository with your original query for deep historical context.
   - **Branch 2**: Automatically shortens your query into concise sub-queries and searches all live platforms (Jira, Confluence, SharePoint, Slack, Notion) for documents posted in the past 4 hours. Results are deduplicated automatically.

2. **Analyze the results**: Review what came back from both the repository (historical) and live platforms (recent). Identify the most relevant information from each.

3. **Self-assessment — do you need full documents?**: After reviewing the fan-out results, ask yourself:
   - Are there search result snippets that are clearly truncated where the full content would **materially change or improve** your answer?
   - Are there `repo_path` values in the results metadata pointing to documents you need to read in full?
   If YES, use `read_full_documents` to batch-retrieve the full content of those documents. This is your **one final deep-dive** — use it only when truly needed, not by default.

4. **Targeted follow-ups (if needed)**: If the fan-out and full-doc retrieval aren't sufficient, use targeted tools:
   - `read_repo_file` to get a specific file from the repo
   - `search_knowledge_repo` with filters (source, info_type) for focused vector search
   - Individual platform search tools for further live follow-ups

5. **Synthesize your answer**: Combine information from multiple sources into a comprehensive, well-structured response.

## Response Guidelines
- **Always cite your sources**: For every piece of information, mention where it came from and include the URL when available. Format as: "According to [Document Title](URL)..." or "A recent Jira issue [PROJ-123](URL) mentions...". Never give unsourced claims.
- **Be comprehensive but concise**: Cover all relevant aspects thoroughly without unnecessary padding.  Give the user a complete picture — do not give half answers.
- **Distinguish between historical and live data**: Clearly note whether information comes from the cached knowledge repository (historical) or from a live platform query (recent, last 4 hours).
- **Acknowledge uncertainty**: If you cannot find sufficient information, say so clearly rather than guessing. Never fabricate sources or URLs.
- **Use structured formatting**: Use headings, bullet points, and bold text to make answers scannable.
"""


class QueryEngine:
    """The coordinator agent that orchestrates query answering."""

    MAX_ROUNDS = 3  # Max follow-up rounds after initial fan-out (fan-out is auto, then up to 3 tool-use rounds + forced synthesis)

    def __init__(
        self,
        anthropic_api_key: str,
        model: str,
        tool_executor: ToolExecutor,
    ):
        self.client = AsyncAnthropic(api_key=anthropic_api_key)
        self.model = model
        self.tool_executor = tool_executor

    MAX_HISTORY_PAIRS = 10  # Max prior Q/A pairs to include (20 messages)

    async def query(self, question: str, history: list[dict] | None = None) -> str:
        """Execute a query and return the complete answer."""
        result_parts = []
        async for chunk in self.query_stream(question, history=history):
            result_parts.append(chunk)
        return "".join(result_parts)

    async def query_stream(self, question: str, history: list[dict] | None = None) -> AsyncGenerator[str, None]:
        """Execute a query with streaming response.

        Implements the three-phase query architecture:
        1. Auto-trigger fan_out_search
        2. Claude synthesizes from gathered context
        3. Optional follow-up rounds for deep-dives

        Args:
            question: The current user question.
            history: Prior conversation messages from the same chat thread.
                     Each entry is {"role": "user"|"assistant", "content": "..."}.
                     Only messages from the current chat are included — no
                     cross-chat contamination.
        """
        start_time = time.time()

        # Phase 1: Auto fan-out search
        logger.info(f"Query received: '{question[:100]}...'")
        fan_out_context = await self.tool_executor.execute("fan_out_search", {"query": question})

        # Build initial messages with optional chat history + fan-out results
        messages = []

        # Prepend prior conversation history (capped to avoid context overflow)
        if history:
            trimmed = history[-(self.MAX_HISTORY_PAIRS * 2):]
            logger.info(f"Including {len(trimmed)} prior messages from chat history")
            for msg in trimmed:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Current question + fan-out results
        messages.extend([
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
        ])

        # Phase 2 & 3: Claude synthesis + optional follow-ups
        for round_num in range(self.MAX_ROUNDS + 1):
            logger.info(f"Agent round {round_num + 2}")  # +2 because fan-out is round 1

            try:
                # Use streaming so text tokens flow to the browser in real-time.
                # When Claude calls a tool we collect the full response (needed to
                # reconstruct the tool_use block), execute it, then stream again.
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                ) as stream:
                    # Stream text tokens to the browser as they arrive
                    async for text in stream.text_stream:
                        yield text

                    # Get the final completed message (needed for tool_use blocks)
                    response = await stream.get_final_message()

            except Exception as e:
                logger.error(f"Claude API error: {e}")
                yield f"\n\n*Error communicating with AI: {e}*"
                return

            # Process non-text blocks (tool_use) from the completed response
            has_tool_use = False
            assistant_content = []
            tool_results = []

            for block in response.content:
                assistant_content.append(block)

                if block.type == "tool_use":
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
        else:
            # Loop exhausted all rounds while Claude was still calling tools.
            # Run one final round WITHOUT tools so Claude is forced to produce
            # a text synthesis from the gathered context.
            logger.info("Max rounds reached — forcing final synthesis")
            
            final_instruction = {
                "type": "text", 
                "text": "You have reached the maximum number of tool calls. Please synthesize a final answer using ONLY the information gathered so far. You MUST NOT use any more tools."
            }
            if messages and messages[-1]["role"] == "user":
                if isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append(final_instruction)
                else:
                    messages[-1]["content"] = [
                        {"type": "text", "text": messages[-1]["content"]},
                        final_instruction
                    ]
            else:
                messages.append({"role": "user", "content": [final_instruction]})
                
            try:
                async with self.client.messages.stream(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
            except Exception as e:
                logger.error(f"Claude API error in final synthesis: {e}")
                yield f"\n\n*Error communicating with AI: {e}*"

        elapsed = time.time() - start_time
        logger.info(f"Query completed in {elapsed:.1f}s")
