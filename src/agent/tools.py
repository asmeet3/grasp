"""Tool definitions for the Claude-powered coordinator agent.

Defines all tools (JSON Schema format for Claude's tool-use API) and
their execution functions. Includes the fan_out_search meta-tool and
individual platform search tools.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from ..connectors.base import Document
from ..index.vector_store import SearchResult

if TYPE_CHECKING:
    from .sub_agents import SubAgentDispatcher
    from ..index.vector_store import VectorStore
    from ..repo.manager import RepoManager
    from ..connectors.base import BaseConnector

logger = logging.getLogger(__name__)


# ── Tool schema definitions for Claude ─────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "fan_out_search",
        "description": (
            "Search ALL sources simultaneously using a two-branch strategy: "
            "(1) the knowledge repository (ChromaDB) is searched with the original query, "
            "(2) the query is shortened into concise sub-queries and used to search live "
            "platforms (Confluence, Jira, SharePoint, Slack, Notion) for docs from the past "
            "4 hours — results are deduplicated automatically. "
            "This is the fastest way to gather broad context. Use as your FIRST action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to send to all sources simultaneously.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_knowledge_repo",
        "description": (
            "Search the indexed knowledge repository using semantic/vector search. "
            "Useful for targeted follow-up searches with optional filters by source or information type. "
            "The repo contains the full historical record across all platforms."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The semantic search query.",
                },
                "source_filter": {
                    "type": "string",
                    "description": "Optional: filter by source (confluence, jira, sharepoint, slack, notion).",
                    "enum": ["confluence", "jira", "sharepoint", "slack", "notion"],
                },
                "info_type_filter": {
                    "type": "string",
                    "description": "Optional: filter by information type.",
                    "enum": [
                        "decisions", "projects", "processes", "products",
                        "people", "topics",
                    ],
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_repo_file",
        "description": (
            "Read the full content of a specific file from the knowledge repository. "
            "Use this when you need the complete text of a document identified by a previous search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file in the knowledge repo (e.g., 'knowledge/decisions/2024-API_Design.md').",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "read_full_documents",
        "description": (
            "Batch-read the full content of documents identified by their repo_path from "
            "previous search results. Use this ONLY when the fan-out search returned "
            "truncated snippets and you believe the full document content would materially "
            "improve your answer. This is your final deep-dive tool — use it sparingly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of repo_path strings from search result metadata "
                        "(e.g., ['knowledge/decisions/2024-API_Design.md', ...]). Max 5."
                    ),
                }
            },
            "required": ["repo_paths"],
        },
    },
    {
        "name": "search_confluence_live",
        "description": "Search Confluence in real-time for the most recent content (last few hours). Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for Confluence."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_jira_live",
        "description": "Search Jira in real-time for recently updated issues. Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for Jira."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_sharepoint_live",
        "description": "Search SharePoint in real-time for recent documents and list items. Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for SharePoint."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_slack_live",
        "description": "Search Slack in real-time for recent messages and threads. Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for Slack."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_notion_live",
        "description": "Search Notion in real-time for recent pages and database entries. Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for Notion."}
            },
            "required": ["query"],
        },
    },
]


class ToolExecutor:
    """Executes tools on behalf of the coordinator agent."""

    def __init__(
        self,
        dispatcher: SubAgentDispatcher,
        vector_store: VectorStore,
        repo_manager: RepoManager,
        connectors: dict[str, BaseConnector],
    ):
        self.dispatcher = dispatcher
        self.vector_store = vector_store
        self.repo_manager = repo_manager
        self.connectors = connectors

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string."""
        try:
            if tool_name == "fan_out_search":
                return await self._fan_out_search(tool_input["query"])
            elif tool_name == "search_knowledge_repo":
                return self._search_repo(
                    tool_input["query"],
                    tool_input.get("source_filter"),
                    tool_input.get("info_type_filter"),
                    tool_input.get("n_results", 10),
                )
            elif tool_name == "read_repo_file":
                return self._read_file(tool_input["file_path"])
            elif tool_name == "read_full_documents":
                return self._read_full_documents(tool_input["repo_paths"])
            elif tool_name == "search_confluence_live":
                return await self._search_live("confluence", tool_input["query"])
            elif tool_name == "search_jira_live":
                return await self._search_live("jira", tool_input["query"])
            elif tool_name == "search_sharepoint_live":
                return await self._search_live("sharepoint", tool_input["query"])
            elif tool_name == "search_slack_live":
                return await self._search_live("slack", tool_input["query"])
            elif tool_name == "search_notion_live":
                return await self._search_live("notion", tool_input["query"])
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool execution failed for {tool_name}: {e}")
            return f"Error executing {tool_name}: {e}"

    async def _fan_out_search(self, query: str) -> str:
        """Execute parallel fan-out search across all sources."""
        results = await self.dispatcher.fan_out(query)
        return self.dispatcher.format_all_results(results)

    def _search_repo(
        self, query: str, source: str | None, info_type: str | None, n: int
    ) -> str:
        """Search the vector store with optional filters."""
        results = self.vector_store.search(
            query=query,
            n_results=n,
            source_filter=source,
            info_type_filter=info_type,
        )

        if not results:
            return "No results found in the knowledge repository."

        lines = [f"Found {len(results)} results in the knowledge repository:"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. **{r.title}** (score: {r.score:.2f})")
            lines.append(f"   Source: {r.source} | Type: {r.info_type}")
            lines.append(f"   File: {r.repo_path}")
            if r.url:
                lines.append(f"   URL: {r.url}")
            lines.append(f"   Content: {r.content[:400]}...")

        return "\n".join(lines)

    def _read_file(self, file_path: str) -> str:
        """Read a file from the knowledge repository."""
        content = self.repo_manager.get_file_content(file_path)
        if content:
            return f"Content of {file_path}:\n\n{content}"
        return f"File not found: {file_path}"

    def _read_full_documents(self, repo_paths: list[str]) -> str:
        """Batch-read full documents from the knowledge repository.

        Used for the follow-up round when Sonnet determines that
        truncated snippets need full context.
        """
        MAX_DOCS = 5
        paths = repo_paths[:MAX_DOCS]

        parts = []
        for path in paths:
            content = self.repo_manager.get_file_content(path)
            if content:
                parts.append(f"--- Full Document: {path} ---\n\n{content}")
            else:
                parts.append(f"--- {path}: NOT FOUND ---")

        if not parts:
            return "No documents could be read from the provided paths."

        return (
            f"Retrieved {len(parts)} full document(s):\n\n"
            + "\n\n".join(parts)
        )

    async def _search_live(self, platform: str, query: str) -> str:
        """Search a specific platform live."""
        connector = self.connectors.get(platform)
        if not connector:
            return f"Platform '{platform}' is not configured."

        try:
            results = await connector.live_search(query)
            if not results:
                return f"No recent results found on {platform}."

            lines = [f"Found {len(results)} recent results on {platform}:"]
            for i, doc in enumerate(results, 1):
                lines.append(f"\n{i}. **{doc.title}**")
                if doc.url:
                    lines.append(f"   URL: {doc.url}")
                lines.append(f"   {doc.content[:400]}...")

            return "\n".join(lines)
        except Exception as e:
            return f"Error searching {platform}: {e}"
