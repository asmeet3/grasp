"""Notion connector — retrieves pages and database items via Notion API.

Uses start_cursor pagination, recursive block fetching, and the
search endpoint for both incremental and live queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import httpx

from .base import BaseConnector, Document

logger = logging.getLogger(__name__)


class NotionConnector(BaseConnector):
    """Connector for Notion via the official API."""

    API_BASE = "https://api.notion.com/v1"
    API_VERSION = "2022-06-28"

    def __init__(self, api_key: str, batch_size: int = 50):
        super().__init__("notion")
        self.api_key = api_key
        self.batch_size = batch_size
        self._checkpoint: dict = {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Notion-Version": self.API_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def _api_post(self, url: str, json: dict | None = None) -> dict:
        client = await self._get_client()
        response = await self.rate_limiter.execute(client, "POST", url, json=json or {})
        return response.json()

    async def _api_get(self, url: str, params: dict | None = None) -> dict:
        client = await self._get_client()
        response = await self.rate_limiter.execute(client, "GET", url, params=params)
        return response.json()

    # ── Full retrieval ─────────────────────────────────────

    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Retrieve all pages and database items from Notion."""
        start_cursor = None
        if checkpoint:
            start_cursor = checkpoint.get("cursor")
            self.logger.info(f"Resuming from checkpoint cursor: {start_cursor}")

        # Use the search endpoint to discover all pages
        async for batch in self._search_all(start_cursor=start_cursor):
            yield batch

        self.logger.info("Full Notion retrieval complete")

    async def _search_all(
        self, start_cursor: str | None = None, query: str = ""
    ) -> AsyncGenerator[list[Document], None]:
        """Search all content in the workspace with pagination."""
        batch: list[Document] = []

        while True:
            payload: dict = {"page_size": 100}
            if query:
                payload["query"] = query
            if start_cursor:
                payload["start_cursor"] = start_cursor

            data = await self._api_post(f"{self.API_BASE}/search", json=payload)

            for result in data.get("results", []):
                doc = await self._result_to_document(result)
                if doc:
                    batch.append(doc)
                    if len(batch) >= self.batch_size:
                        self._checkpoint = {"cursor": data.get("next_cursor")}
                        yield batch
                        batch = []

            if not data.get("has_more", False):
                break

            start_cursor = data.get("next_cursor")
            if not start_cursor:
                break

        if batch:
            yield batch

    # ── Incremental retrieval ──────────────────────────────

    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Retrieve pages edited since the given timestamp."""
        since_iso = since.isoformat()

        # Use search with sort by last_edited_time and filter
        start_cursor = None
        batch: list[Document] = []

        while True:
            payload: dict = {
                "page_size": 100,
                "sort": {
                    "direction": "descending",
                    "timestamp": "last_edited_time",
                },
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor

            data = await self._api_post(f"{self.API_BASE}/search", json=payload)

            found_old = False
            for result in data.get("results", []):
                last_edited = result.get("last_edited_time", "")
                if last_edited and last_edited < since_iso:
                    found_old = True
                    break

                doc = await self._result_to_document(result)
                if doc:
                    batch.append(doc)
                    if len(batch) >= self.batch_size:
                        yield batch
                        batch = []

            if found_old or not data.get("has_more", False):
                break

            start_cursor = data.get("next_cursor")
            if not start_cursor:
                break

        if batch:
            yield batch

    # ── Live search ────────────────────────────────────────

    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search Notion for pages matching the query."""
        results = []
        async for batch in self._search_all(query=query):
            results.extend(batch)
            if len(results) >= 10:
                break

        return results[:10]

    # ── Document conversion ────────────────────────────────

    async def _result_to_document(self, result: dict) -> Document | None:
        """Convert a Notion search result to a Document."""
        try:
            object_type = result.get("object", "")
            result_id = result.get("id", "")

            if object_type == "page":
                return await self._page_to_document(result)
            elif object_type == "database":
                return self._database_to_document(result)
            else:
                return None
        except Exception as e:
            self.logger.warning(f"Failed to parse Notion result: {e}")
            return None

    async def _page_to_document(self, page: dict) -> Document | None:
        """Convert a Notion page to a Document, fetching its block content."""
        page_id = page.get("id", "")
        title = self._extract_title(page)
        url = page.get("url", "")

        # Fetch the page's block content
        content = await self._get_page_blocks(page_id)

        last_edited = page.get("last_edited_time", "")
        updated_at = datetime.now(timezone.utc)
        if last_edited:
            try:
                updated_at = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Extract parent info for context
        parent = page.get("parent", {})
        parent_type = parent.get("type", "unknown")

        return Document(
            id=f"notion-{page_id}",
            source="notion",
            title=title,
            content=content,
            url=url,
            updated_at=updated_at,
            metadata={
                "page_id": page_id,
                "parent_type": parent_type,
                "object_type": "page",
            },
        )

    def _database_to_document(self, db: dict) -> Document | None:
        """Convert a Notion database metadata to a Document."""
        db_id = db.get("id", "")
        title = self._extract_title(db)
        url = db.get("url", "")
        description = self._extract_rich_text(db.get("description", []))

        # List database properties as content
        props = db.get("properties", {})
        prop_lines = []
        for name, prop in props.items():
            prop_type = prop.get("type", "unknown")
            prop_lines.append(f"- **{name}** ({prop_type})")

        content_parts = []
        if description:
            content_parts.append(description)
        if prop_lines:
            content_parts.append("\n## Properties\n" + "\n".join(prop_lines))

        last_edited = db.get("last_edited_time", "")
        updated_at = datetime.now(timezone.utc)
        if last_edited:
            try:
                updated_at = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
            except ValueError:
                pass

        return Document(
            id=f"notion-db-{db_id}",
            source="notion",
            title=f"[Database] {title}",
            content="\n\n".join(content_parts) if content_parts else f"Database: {title}",
            url=url,
            updated_at=updated_at,
            metadata={"database_id": db_id, "object_type": "database"},
        )

    # ── Block content fetching ─────────────────────────────

    async def _get_page_blocks(self, page_id: str, depth: int = 0) -> str:
        """Recursively fetch all blocks for a page and convert to Markdown."""
        if depth > 3:  # Prevent infinite recursion
            return ""

        parts = []
        start_cursor = None

        while True:
            params: dict = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor

            url = f"{self.API_BASE}/blocks/{page_id}/children"
            data = await self._api_get(url, params=params)

            for block in data.get("results", []):
                text = self._block_to_markdown(block)
                if text:
                    parts.append(text)

                # Recurse into children if the block has them
                if block.get("has_children", False):
                    child_content = await self._get_page_blocks(block["id"], depth + 1)
                    if child_content:
                        # Indent child content
                        indented = "\n".join("  " + line for line in child_content.splitlines())
                        parts.append(indented)

            if not data.get("has_more", False):
                break

            start_cursor = data.get("next_cursor")
            if not start_cursor:
                break

        return "\n\n".join(parts)

    def _block_to_markdown(self, block: dict) -> str:
        """Convert a single Notion block to Markdown."""
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})

        if block_type in ("paragraph", "quote", "callout"):
            text = self._extract_rich_text(block_data.get("rich_text", []))
            if block_type == "quote":
                return "> " + text if text else ""
            return text

        elif block_type.startswith("heading_"):
            level = int(block_type[-1])
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{'#' * level} {text}" if text else ""

        elif block_type == "bulleted_list_item":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"- {text}" if text else ""

        elif block_type == "numbered_list_item":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"1. {text}" if text else ""

        elif block_type == "to_do":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            checked = "x" if block_data.get("checked", False) else " "
            return f"- [{checked}] {text}" if text else ""

        elif block_type == "code":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            lang = block_data.get("language", "")
            return f"```{lang}\n{text}\n```" if text else ""

        elif block_type == "divider":
            return "---"

        elif block_type == "toggle":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"<details><summary>{text}</summary></details>" if text else ""

        elif block_type == "table_row":
            cells = block_data.get("cells", [])
            row = " | ".join(self._extract_rich_text(cell) for cell in cells)
            return f"| {row} |"

        return ""

    # ── Rich text extraction ───────────────────────────────

    def _extract_title(self, obj: dict) -> str:
        """Extract the title from a Notion page or database."""
        properties = obj.get("properties", {})
        for prop_name, prop_value in properties.items():
            if prop_value.get("type") == "title":
                title_arr = prop_value.get("title", [])
                return self._extract_rich_text(title_arr)

        # Fallback: try the title field directly
        title_arr = obj.get("title", [])
        if title_arr:
            return self._extract_rich_text(title_arr)

        return "Untitled"

    def _extract_rich_text(self, rich_text: list[dict]) -> str:
        """Convert Notion rich text array to plain text with basic formatting."""
        parts = []
        for segment in rich_text:
            text = segment.get("plain_text", "")
            annotations = segment.get("annotations", {})

            if annotations.get("bold"):
                text = f"**{text}**"
            if annotations.get("italic"):
                text = f"*{text}*"
            if annotations.get("code"):
                text = f"`{text}`"
            if annotations.get("strikethrough"):
                text = f"~~{text}~~"

            href = segment.get("href")
            if href:
                text = f"[{text}]({href})"

            parts.append(text)

        return "".join(parts)

    # ── Checkpoint ─────────────────────────────────────────

    def get_checkpoint_state(self) -> dict:
        return dict(self._checkpoint)

    async def health_check(self) -> bool:
        try:
            await self._api_get(f"{self.API_BASE}/users/me")
            return True
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
