"""Jira connector — retrieves issues from Jira Cloud via REST API v3.

Uses nextPageToken-based pagination, JQL for filtering, and extracts
issue descriptions, comments, and metadata.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import httpx

from .base import BaseConnector, Document, html_to_markdown

logger = logging.getLogger(__name__)


class JiraConnector(BaseConnector):
    """Connector for Atlassian Jira Cloud."""

    def __init__(self, base_url: str, email: str, api_token: str, batch_size: int = 50):
        super().__init__("jira")
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.batch_size = batch_size
        self._checkpoint: dict = {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                auth=(self.email, self.api_token),
                headers={"Accept": "application/json"},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def _api_get(self, url: str, params: dict | None = None) -> dict:
        client = await self._get_client()
        response = await self.rate_limiter.execute(client, "GET", url, params=params)
        return response.json()

    async def _api_post(self, url: str, json_body: dict) -> dict:
        """POST request for Enhanced JQL search endpoint."""
        client = await self._get_client()
        response = await self.rate_limiter.execute(client, "POST", url, json=json_body)
        return response.json()

    # ── Full retrieval ─────────────────────────────────────

    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Retrieve all Jira issues across the instance."""
        # Enhanced JQL endpoint requires a restriction — cannot use bare ORDER BY
        jql = 'created >= "2000-01-01" ORDER BY created ASC'
        next_page_token = None

        if checkpoint:
            next_page_token = checkpoint.get("next_page_token")
            self.logger.info(f"Resuming from checkpoint token: {next_page_token}")

        async for batch in self._search_issues(jql, next_page_token=next_page_token):
            yield batch

        self.logger.info("Full Jira retrieval complete")

    # ── Incremental retrieval ──────────────────────────────

    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Retrieve issues updated since the given timestamp."""
        since_str = since.strftime("%Y-%m-%d %H:%M")
        jql = f'updated >= "{since_str}" ORDER BY updated ASC'

        async for batch in self._search_issues(jql):
            yield batch

    # ── Live search ────────────────────────────────────────

    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search Jira for recently updated issues matching the query."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_str = since.strftime("%Y-%m-%d %H:%M")
        jql = f'text ~ "{query}" AND updated >= "{since_str}" ORDER BY updated DESC'

        results = []
        async for batch in self._search_issues(jql, max_results=10):
            results.extend(batch)
            if len(results) >= 10:
                break

        return results[:10]

    # ── JQL search helper ──────────────────────────────────

    async def _search_issues(
        self,
        jql: str,
        next_page_token: str | None = None,
        max_results: int | None = None,
    ) -> AsyncGenerator[list[Document], None]:
        """Search issues via Enhanced JQL (POST /search/jql) with cursor pagination."""
        url = f"{self.base_url}/rest/api/3/search/jql"
        total_fetched = 0

        while True:
            page_size = min(self.batch_size, max_results - total_fetched if max_results else self.batch_size)
            body = {
                "jql": jql,
                "maxResults": page_size,
                "fields": [
                    "summary", "description", "status", "assignee", "reporter",
                    "priority", "project", "comment", "issuetype", "created",
                    "updated", "labels",
                ],
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token

            data = await self._api_post(url, json_body=body)

            batch: list[Document] = []
            for issue in data.get("issues", []):
                doc = self._issue_to_document(issue)
                if doc:
                    batch.append(doc)
                    total_fetched += 1

            if batch:
                next_page_token = data.get("nextPageToken")
                self._checkpoint = {"next_page_token": next_page_token}
                yield batch

            if max_results and total_fetched >= max_results:
                return

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

    # ── Document conversion ────────────────────────────────

    def _issue_to_document(self, issue: dict) -> Document | None:
        """Convert a Jira issue to a Document with full context."""
        try:
            key = issue.get("key", "")
            fields = issue.get("fields", {})

            title = f"[{key}] {fields.get('summary', 'Untitled')}"
            project_key = fields.get("project", {}).get("key", "unknown")

            # Build content from description + metadata + comments
            parts = []

            # Issue metadata header
            status = fields.get("status", {}).get("name", "Unknown")
            issue_type = fields.get("issuetype", {}).get("name", "Unknown")
            priority = fields.get("priority", {}).get("name", "None")
            assignee = fields.get("assignee", {})
            assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
            reporter = fields.get("reporter", {})
            reporter_name = reporter.get("displayName", "Unknown") if reporter else "Unknown"
            labels = fields.get("labels", [])

            parts.append(f"**Type:** {issue_type}  ")
            parts.append(f"**Status:** {status}  ")
            parts.append(f"**Priority:** {priority}  ")
            parts.append(f"**Assignee:** {assignee_name}  ")
            parts.append(f"**Reporter:** {reporter_name}  ")
            if labels:
                parts.append(f"**Labels:** {', '.join(labels)}  ")
            parts.append("")

            # Description
            desc = fields.get("description")
            if desc:
                desc_text = self._adf_to_text(desc) if isinstance(desc, dict) else str(desc)
                parts.append("## Description\n")
                parts.append(desc_text)
                parts.append("")

            # Comments
            comment_data = fields.get("comment", {})
            comments = comment_data.get("comments", []) if isinstance(comment_data, dict) else []
            if comments:
                parts.append("## Comments\n")
                for comment in comments:
                    author = comment.get("author", {}).get("displayName", "Unknown")
                    created = comment.get("created", "")
                    body = comment.get("body", {})
                    body_text = self._adf_to_text(body) if isinstance(body, dict) else str(body)
                    parts.append(f"**{author}** ({created}):\n{body_text}\n")

            content = "\n".join(parts)

            # Parse timestamp
            updated_str = fields.get("updated", "")
            updated_at = datetime.now(timezone.utc)
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            issue_url = f"{self.base_url}/browse/{key}"

            return Document(
                id=f"jira-{key}",
                source="jira",
                title=title,
                content=content,
                url=issue_url,
                updated_at=updated_at,
                metadata={
                    "project_key": project_key,
                    "issue_key": key,
                    "status": status,
                    "issue_type": issue_type,
                    "priority": priority,
                },
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse Jira issue: {e}")
            return None

    def _adf_to_text(self, adf: dict) -> str:
        """Convert Atlassian Document Format (ADF) to plain text."""
        if not isinstance(adf, dict):
            return str(adf)

        parts = []

        def _walk(node: dict | list | str):
            if isinstance(node, str):
                parts.append(node)
                return
            if isinstance(node, list):
                for item in node:
                    _walk(item)
                return
            if not isinstance(node, dict):
                return

            node_type = node.get("type", "")
            content = node.get("content", [])

            if node_type == "text":
                parts.append(node.get("text", ""))
            elif node_type == "hardBreak":
                parts.append("\n")
            elif node_type in ("paragraph", "heading"):
                _walk(content)
                parts.append("\n\n")
            elif node_type in ("bulletList", "orderedList"):
                _walk(content)
                parts.append("\n")
            elif node_type == "listItem":
                parts.append("- ")
                _walk(content)
            elif node_type == "codeBlock":
                parts.append("```\n")
                _walk(content)
                parts.append("\n```\n")
            else:
                _walk(content)

        _walk(adf)
        return "".join(parts).strip()

    # ── Checkpoint ─────────────────────────────────────────

    def get_checkpoint_state(self) -> dict:
        return dict(self._checkpoint)

    async def health_check(self) -> bool:
        try:
            await self._api_get(f"{self.base_url}/rest/api/3/myself")
            return True
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
