"""Tool definitions for the Claude-powered coordinator agent.

Defines all tools (JSON Schema format for Claude's tool-use API) and
their execution functions. Includes the fan_out_search meta-tool and
individual platform search tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..core.security import AuthContext, PolicyEngine

if TYPE_CHECKING:
    from ..connectors.base import BaseConnector
    from ..index.vector_store import VectorStore
    from ..memory import StructuredMemoryService
    from ..repo.manager import RepoManager
    from .sub_agents import SubAgentDispatcher

logger = logging.getLogger(__name__)


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
                        "decisions",
                        "projects",
                        "processes",
                        "products",
                        "people",
                        "topics",
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
                    "description": (
                        "Relative path to the file in the knowledge repo, such as "
                        "'knowledge/decisions/2024-API_Design.md'."
                    ),
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "read_full_documents",
        "description": (
            "Batch-read the full content of documents identified by their repo_path from "
            "previous search results when their truncated snippets do not provide enough context."
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
        "description": (
            "Search Confluence in real time for recent content. Use for targeted follow-up."
        ),
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
            "properties": {"query": {"type": "string", "description": "Search query for Jira."}},
            "required": ["query"],
        },
    },
    {
        "name": "search_sharepoint_live",
        "description": (
            "Search SharePoint in real time for recent documents and list items. "
            "Use for targeted follow-up."
        ),
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
            "properties": {"query": {"type": "string", "description": "Search query for Slack."}},
            "required": ["query"],
        },
    },
    {
        "name": "search_notion_live",
        "description": "Search Notion in real-time for recent pages and database entries. Use for targeted follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query for Notion."}},
            "required": ["query"],
        },
    },
]

MEMORY_TOOL_DEFINITION = {
    "name": "search_memory",
    "description": (
        "Search the structured organizational memory for known entities "
        "(people, teams, projects, products, decisions, technologies, etc.) "
        "and their relationships. Use when the question involves "
        "organizational structure, ownership, project status, team "
        "composition, or decision history. Returns entity details "
        "and connections."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Entity name or search term.",
            },
            "entity_type": {
                "type": "string",
                "description": "Optional: filter by entity type.",
                "enum": [
                    "person",
                    "team",
                    "project",
                    "product",
                    "process",
                    "technology",
                    "decision",
                    "milestone",
                ],
            },
        },
        "required": ["query"],
    },
}


class ToolExecutor:
    """Executes tools on behalf of the coordinator agent."""

    def __init__(
        self,
        dispatcher: SubAgentDispatcher,
        vector_store: VectorStore,
        repo_manager: RepoManager,
        connectors: dict[str, BaseConnector],
        memory_service: StructuredMemoryService | None = None,
    ):
        self.dispatcher = dispatcher
        self.vector_store = vector_store
        self.repo_manager = repo_manager
        self.connectors = connectors
        self.memory_service = memory_service
        self.policy = PolicyEngine()

    @property
    def tool_definitions(self) -> list[dict]:
        """Return tool definitions, including memory tool when enabled."""
        tools = list(TOOL_DEFINITIONS)
        if self.memory_service is not None:
            tools.append(MEMORY_TOOL_DEFINITION)
        return tools

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        auth_context: AuthContext | None = None,
    ) -> str:
        """Execute a tool and return the result as a string."""
        if auth_context is None:
            return "Access denied: authenticated policy context is required."
        try:
            if tool_name == "fan_out_search":
                return await self._fan_out_search(tool_input["query"], auth_context)
            elif tool_name == "search_knowledge_repo":
                return await self._search_repo(
                    tool_input["query"],
                    tool_input.get("source_filter"),
                    tool_input.get("info_type_filter"),
                    tool_input.get("n_results", 10),
                    auth_context,
                )
            elif tool_name == "read_repo_file":
                return await self._read_file(tool_input["file_path"], auth_context)
            elif tool_name == "read_full_documents":
                return await self._read_full_documents(tool_input["repo_paths"], auth_context)
            elif tool_name == "search_confluence_live":
                return await self._search_live("confluence", tool_input["query"], auth_context)
            elif tool_name == "search_jira_live":
                return await self._search_live("jira", tool_input["query"], auth_context)
            elif tool_name == "search_sharepoint_live":
                return await self._search_live("sharepoint", tool_input["query"], auth_context)
            elif tool_name == "search_slack_live":
                return await self._search_live("slack", tool_input["query"], auth_context)
            elif tool_name == "search_notion_live":
                return await self._search_live("notion", tool_input["query"], auth_context)
            elif tool_name == "search_memory":
                return await self._search_memory(
                    tool_input["query"],
                    tool_input.get("entity_type"),
                    auth_context,
                )
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool execution failed for {tool_name}: {e}")
            return f"Error executing {tool_name}: {e}"

    async def _fan_out_search(self, query: str, auth_context: AuthContext) -> str:
        """Execute parallel fan-out search across all sources."""
        results = await self.dispatcher.fan_out(query, auth_context)
        return self.dispatcher.format_all_results(results)

    async def _search_repo(
        self,
        query: str,
        source: str | None,
        info_type: str | None,
        n: int,
        auth_context: AuthContext,
    ) -> str:
        """Search the vector store with optional filters."""
        import asyncio

        results = await asyncio.to_thread(
            self.vector_store.search,
            query,
            n_results=n,
            source_filter=source,
            info_type_filter=info_type,
            auth_context=auth_context,
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

    async def _read_file(self, file_path: str, auth_context: AuthContext) -> str:
        """Read a file from the knowledge repository."""
        import asyncio

        metadata = await asyncio.to_thread(self.repo_manager.get_committed_file_metadata, file_path)
        if not self.policy.can_access_document(auth_context, metadata):
            return "Access denied or file not found."
        content = await asyncio.to_thread(self.repo_manager.read_committed_file, file_path)
        if content:
            return f"Content of {file_path}:\n\n{content}"
        return f"File not found: {file_path}"

    async def _read_full_documents(self, repo_paths: list[str], auth_context: AuthContext) -> str:
        """Batch-read full documents from the knowledge repository.

        Used for the follow-up round when Sonnet determines that
        truncated snippets need full context.
        """
        MAX_DOCS = 5
        paths = repo_paths[:MAX_DOCS]

        import asyncio

        def read_documents() -> list[str]:
            parts: list[str] = []
            for path in paths:
                metadata = self.repo_manager.get_committed_file_metadata(path)
                content = (
                    self.repo_manager.read_committed_file(path)
                    if self.policy.can_access_document(auth_context, metadata)
                    else ""
                )
                if content:
                    parts.append(f"--- Full Document: {path} ---\n\n{content}")
                else:
                    parts.append(f"--- {path}: NOT FOUND ---")
            return parts

        parts = await asyncio.to_thread(read_documents)

        if not parts:
            return "No documents could be read from the provided paths."

        return f"Retrieved {len(parts)} full document(s):\n\n" + "\n\n".join(parts)

    async def _search_live(self, platform: str, query: str, auth_context: AuthContext) -> str:
        """Search a specific platform live."""
        connector = self.connectors.get(platform)
        if not connector:
            return f"Platform '{platform}' is not configured."

        try:
            raw_results = await connector.live_search(query)
            results = [
                doc
                for doc in raw_results
                if self.policy.can_access_document(auth_context, doc.metadata)
            ]
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

    async def _search_memory(
        self,
        query: str,
        entity_type: str | None,
        auth_context: AuthContext,
    ) -> str:
        """Search structured organizational memory for entities."""
        if not self.memory_service:
            return "Structured memory is not enabled."

        entities = await self.memory_service.search_entities(
            auth_context, query, entity_type=entity_type, limit=10
        )

        if not entities:
            return f"No entities found in organizational memory matching '{query}'."

        lines = [f"Found {len(entities)} entities in organizational memory:"]
        for i, entity in enumerate(entities, 1):
            lines.append(f"\n{i}. **{entity['canonical_name']}** ({entity['entity_type']})")
            lines.append(f"   Confidence: {entity['confidence']}")
            aliases = entity.get("aliases") or []
            if aliases:
                lines.append(f"   Also known as: {', '.join(aliases)}")
            attrs = entity.get("attributes") or {}
            if attrs:
                attr_str = ", ".join(f"{k}: {v}" for k, v in attrs.items())
                lines.append(f"   Attributes: {attr_str}")

            # Fetch relationships for this entity
            rels = await self.memory_service.find_relationships(
                auth_context, entity["id"]
            )
            if rels:
                rel_lines = []
                for rel in rels[:5]:  # Cap at 5 per entity
                    direction = "→" if rel["source_entity_id"] == entity["id"] else "←"
                    rel_lines.append(
                        f"{direction} {rel['relationship_type']}"
                    )
                lines.append(f"   Relationships: {'; '.join(rel_lines)}")

        return "\n".join(lines)
