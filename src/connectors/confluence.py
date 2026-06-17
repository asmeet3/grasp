"""Confluence connector — retrieves pages from Confluence Cloud via REST API v2.

Uses cursor-based pagination, HTML-to-Markdown conversion, and CQL for
both incremental and live search modes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import httpx

from .base import BaseConnector, Document, html_to_markdown

logger = logging.getLogger(__name__)


class ConfluenceConnector(BaseConnector):
    """Connector for Atlassian Confluence Cloud."""

    def __init__(self, base_url: str, email: str, api_token: str, batch_size: int = 50):
        super().__init__("confluence")
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

    # ── Full retrieval ─────────────────────────────────────

    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Retrieve all Confluence pages across all spaces."""
        # Determine starting point from checkpoint
        processed_spaces: set[str] = set()
        resume_cursor: str | None = None
        resume_space: str | None = None

        if checkpoint:
            processed_spaces = set(checkpoint.get("processed_spaces", []))
            resume_space = checkpoint.get("current_space")
            resume_cursor = checkpoint.get("cursor")
            self.logger.info(f"Resuming from checkpoint: space={resume_space}, processed={len(processed_spaces)}")

        # Get all spaces
        spaces = await self._get_all_spaces()
        self.logger.info(f"Found {len(spaces)} spaces to process")

        for space in spaces:
            space_key = space["key"]

            # Skip already processed spaces
            if space_key in processed_spaces and space_key != resume_space:
                continue

            cursor = resume_cursor if space_key == resume_space else None
            resume_cursor = None  # Only use checkpoint cursor for the resume space

            self.logger.info(f"Processing space: {space_key}")

            async for batch in self._get_space_pages(space["id"], space_key, cursor):
                yield batch

            processed_spaces.add(space_key)
            self._checkpoint = {
                "processed_spaces": list(processed_spaces),
                "current_space": None,
                "cursor": None,
            }

        self.logger.info("Full Confluence retrieval complete")

    async def _get_all_spaces(self) -> list[dict]:
        """Fetch all Confluence spaces."""
        spaces = []
        url = f"{self.base_url}/api/v2/spaces"
        params = {"limit": 100}

        while url:
            data = await self._api_get(url, params=params)
            spaces.extend(data.get("results", []))
            next_link = data.get("_links", {}).get("next")
            if next_link:
                url = f"{self.base_url}{next_link}" if next_link.startswith("/") else next_link
                params = None  # Cursor is in the URL
            else:
                url = None

        return spaces

    async def _get_space_pages(
        self, space_id: str, space_key: str, resume_cursor: str | None = None
    ) -> AsyncGenerator[list[Document], None]:
        """Fetch all pages in a space, yielding in batches."""
        # Get pages for this space
        url = f"{self.base_url}/api/v2/spaces/{space_id}/pages"
        params = {"limit": self.batch_size, "body-format": "storage"}

        if resume_cursor:
            url = f"{self.base_url}{resume_cursor}"
            params = None

        batch: list[Document] = []

        while url:
            data = await self._api_get(url, params=params)

            for page in data.get("results", []):
                doc = self._page_to_document(page, space_key)
                if doc:
                    batch.append(doc)

                if len(batch) >= self.batch_size:
                    self._checkpoint = {
                        "current_space": space_key,
                        "cursor": data.get("_links", {}).get("next"),
                    }
                    yield batch
                    batch = []

            next_link = data.get("_links", {}).get("next")
            if next_link:
                url = f"{self.base_url}{next_link}" if next_link.startswith("/") else next_link
                params = None
            else:
                url = None

        if batch:
            yield batch

    # ── Incremental retrieval ──────────────────────────────

    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Retrieve pages modified since the given timestamp using CQL."""
        since_str = since.strftime("%Y-%m-%d %H:%M")
        cql = f'lastModified >= "{since_str}" ORDER BY lastModified ASC'

        async for batch in self._search_with_cql(cql):
            yield batch

    # ── Live search ────────────────────────────────────────

    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search Confluence for recent content matching the query."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_str = since.strftime("%Y-%m-%d %H:%M")
        cql = f'text ~ "{query}" AND lastModified >= "{since_str}" ORDER BY lastModified DESC'

        results = []
        async for batch in self._search_with_cql(cql, max_results=10):
            results.extend(batch)
            if len(results) >= 10:
                break

        return results[:10]

    # ── CQL search helper ──────────────────────────────────

    async def _search_with_cql(
        self, cql: str, max_results: int | None = None
    ) -> AsyncGenerator[list[Document], None]:
        """Execute a CQL search and yield document batches."""
        url = f"{self.base_url}/rest/api/content/search"
        params = {
            "cql": cql,
            "limit": min(self.batch_size, max_results or self.batch_size),
            "expand": "body.storage,version,space",
        }

        total_fetched = 0
        batch: list[Document] = []

        while url:
            data = await self._api_get(url, params=params)

            for result in data.get("results", []):
                doc = self._content_to_document(result)
                if doc:
                    batch.append(doc)
                    total_fetched += 1

                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []

                if max_results and total_fetched >= max_results:
                    if batch:
                        yield batch
                    return

            next_link = data.get("_links", {}).get("next")
            if next_link:
                url = f"{self.base_url}{next_link}" if next_link.startswith("/") else next_link
                params = None
            else:
                url = None

        if batch:
            yield batch

    # ── Document conversion ────────────────────────────────

    def _page_to_document(self, page: dict, space_key: str) -> Document | None:
        """Convert a V2 API page response to a Document."""
        try:
            page_id = page.get("id", "")
            title = page.get("title", "Untitled")
            body_html = page.get("body", {}).get("storage", {}).get("value", "")
            content = html_to_markdown(body_html)

            # Build the page URL
            page_url = f"{self.base_url}/spaces/{space_key}/pages/{page_id}"
            web_ui = page.get("_links", {}).get("webui", "")
            if web_ui:
                page_url = f"{self.base_url}{web_ui}"

            updated_str = page.get("version", {}).get("createdAt", "")
            updated_at = datetime.now(timezone.utc)
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            return Document(
                id=f"confluence-{page_id}",
                source="confluence",
                title=title,
                content=content,
                url=page_url,
                updated_at=updated_at,
                metadata={"space_key": space_key, "page_id": page_id},
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse Confluence page: {e}")
            return None

    def _content_to_document(self, result: dict) -> Document | None:
        """Convert a V1 content search result to a Document."""
        try:
            content_id = result.get("id", "")
            title = result.get("title", "Untitled")
            body_html = result.get("body", {}).get("storage", {}).get("value", "")
            content = html_to_markdown(body_html)

            space_key = result.get("space", {}).get("key", "unknown")

            web_ui = result.get("_links", {}).get("webui", "")
            page_url = f"{self.base_url}{web_ui}" if web_ui else f"{self.base_url}/pages/{content_id}"

            version = result.get("version", {})
            updated_str = version.get("when", "")
            updated_at = datetime.now(timezone.utc)
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            return Document(
                id=f"confluence-{content_id}",
                source="confluence",
                title=title,
                content=content,
                url=page_url,
                updated_at=updated_at,
                metadata={"space_key": space_key, "content_id": content_id},
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse Confluence content: {e}")
            return None

    # ── Checkpoint ─────────────────────────────────────────

    def get_checkpoint_state(self) -> dict:
        return dict(self._checkpoint)

    async def health_check(self) -> bool:
        try:
            await self._api_get(f"{self.base_url}/api/v2/spaces", params={"limit": 1})
            return True
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
