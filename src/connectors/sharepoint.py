"""SharePoint connector — retrieves files and list items via Microsoft Graph API.

Uses OAuth2 client credentials, delta queries for incremental sync,
and the Microsoft Search API for live queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import httpx

from .base import BaseConnector, Document

logger = logging.getLogger(__name__)


class SharePointConnector(BaseConnector):
    """Connector for SharePoint Online via Microsoft Graph API."""

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site_id: str,
        batch_size: int = 50,
    ):
        super().__init__("sharepoint")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.site_id = site_id
        self.batch_size = batch_size
        self._checkpoint: dict = {}
        self._access_token: str | None = None
        self._token_expires: datetime = datetime.min.replace(tzinfo=timezone.utc)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def _ensure_token(self):
        """Acquire or refresh the OAuth2 access token."""
        if self._access_token and datetime.now(timezone.utc) < self._token_expires:
            return

        client = await self._get_client()
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }

        response = await client.post(token_url, data=data)
        response.raise_for_status()
        token_data = response.json()

        self._access_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 3600))
        self._token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
        self.logger.info("SharePoint access token acquired/refreshed")

    async def _api_get(self, url: str, params: dict | None = None) -> dict:
        await self._ensure_token()
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = await self.rate_limiter.execute(client, "GET", url, headers=headers, params=params)
        return response.json()

    # ── Full retrieval ─────────────────────────────────────

    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Retrieve all SharePoint content: drive items + list items."""
        processed_drives: set[str] = set()
        processed_lists: set[str] = set()

        if checkpoint:
            processed_drives = set(checkpoint.get("processed_drives", []))
            processed_lists = set(checkpoint.get("processed_lists", []))

        # 1. Retrieve all drive items (files)
        async for batch in self._retrieve_drive_items(processed_drives):
            yield batch

        # 2. Retrieve all list items
        async for batch in self._retrieve_list_items(processed_lists):
            yield batch

        self.logger.info("Full SharePoint retrieval complete")

    async def _retrieve_drive_items(
        self, processed_drives: set[str]
    ) -> AsyncGenerator[list[Document], None]:
        """Retrieve files from all drives in the site."""
        # Get all drives
        url = f"{self.GRAPH_BASE}/sites/{self.site_id}/drives"
        data = await self._api_get(url)

        for drive in data.get("value", []):
            drive_id = drive["id"]
            drive_name = drive.get("name", "Unknown Drive")

            if drive_id in processed_drives:
                continue

            self.logger.info(f"Processing drive: {drive_name}")

            # List all items in the drive recursively
            items_url = f"{self.GRAPH_BASE}/drives/{drive_id}/root/children"
            async for batch in self._walk_drive_folder(drive_id, items_url, drive_name):
                yield batch

            processed_drives.add(drive_id)
            self._checkpoint["processed_drives"] = list(processed_drives)

    async def _walk_drive_folder(
        self, drive_id: str, url: str, drive_name: str, path: str = ""
    ) -> AsyncGenerator[list[Document], None]:
        """Recursively walk a drive folder and yield document batches."""
        batch: list[Document] = []

        while url:
            data = await self._api_get(url)

            for item in data.get("value", []):
                if item.get("folder"):
                    # Recurse into subfolders
                    child_url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{item['id']}/children"
                    child_path = f"{path}/{item['name']}" if path else item['name']
                    async for child_batch in self._walk_drive_folder(drive_id, child_url, drive_name, child_path):
                        yield child_batch
                elif item.get("file"):
                    doc = await self._drive_item_to_document(item, drive_name, path)
                    if doc:
                        batch.append(doc)
                        if len(batch) >= self.batch_size:
                            yield batch
                            batch = []

            url = data.get("@odata.nextLink")

        if batch:
            yield batch

    async def _retrieve_list_items(
        self, processed_lists: set[str]
    ) -> AsyncGenerator[list[Document], None]:
        """Retrieve items from all SharePoint lists."""
        url = f"{self.GRAPH_BASE}/sites/{self.site_id}/lists"
        data = await self._api_get(url)

        for sp_list in data.get("value", []):
            list_id = sp_list["id"]
            list_name = sp_list.get("displayName", "Unknown List")

            if list_id in processed_lists:
                continue

            # Skip system lists
            if sp_list.get("list", {}).get("hidden", False):
                continue

            self.logger.info(f"Processing list: {list_name}")

            items_url = f"{self.GRAPH_BASE}/sites/{self.site_id}/lists/{list_id}/items"
            params = {"expand": "fields", "$top": str(self.batch_size)}
            batch: list[Document] = []

            while items_url:
                item_data = await self._api_get(items_url, params=params)

                for item in item_data.get("value", []):
                    doc = self._list_item_to_document(item, list_name)
                    if doc:
                        batch.append(doc)
                        if len(batch) >= self.batch_size:
                            yield batch
                            batch = []

                items_url = item_data.get("@odata.nextLink")
                params = None  # Pagination URL includes params

            if batch:
                yield batch

            processed_lists.add(list_id)
            self._checkpoint["processed_lists"] = list(processed_lists)

    # ── Incremental retrieval ──────────────────────────────

    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Retrieve items changed since the given timestamp."""
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Delta for drives
        url = f"{self.GRAPH_BASE}/sites/{self.site_id}/drives"
        drives_data = await self._api_get(url)

        for drive in drives_data.get("value", []):
            drive_id = drive["id"]
            drive_name = drive.get("name", "Unknown Drive")
            delta_url = f"{self.GRAPH_BASE}/drives/{drive_id}/root/delta"
            params = {"$filter": f"lastModifiedDateTime ge {since_str}"}

            batch: list[Document] = []
            while delta_url:
                data = await self._api_get(delta_url, params=params)
                for item in data.get("value", []):
                    if item.get("file"):
                        doc = await self._drive_item_to_document(item, drive_name, "")
                        if doc:
                            batch.append(doc)
                            if len(batch) >= self.batch_size:
                                yield batch
                                batch = []
                delta_url = data.get("@odata.nextLink")
                params = None
            if batch:
                yield batch

        # Incremental for lists
        lists_data = await self._api_get(f"{self.GRAPH_BASE}/sites/{self.site_id}/lists")
        for sp_list in lists_data.get("value", []):
            if sp_list.get("list", {}).get("hidden", False):
                continue
            list_id = sp_list["id"]
            list_name = sp_list.get("displayName", "Unknown List")

            items_url = f"{self.GRAPH_BASE}/sites/{self.site_id}/lists/{list_id}/items"
            params = {
                "expand": "fields",
                "$filter": f"lastModifiedDateTime ge {since_str}",
                "$top": str(self.batch_size),
            }

            batch = []
            while items_url:
                data = await self._api_get(items_url, params=params)
                for item in data.get("value", []):
                    doc = self._list_item_to_document(item, list_name)
                    if doc:
                        batch.append(doc)
                        if len(batch) >= self.batch_size:
                            yield batch
                            batch = []
                items_url = data.get("@odata.nextLink")
                params = None
            if batch:
                yield batch

    # ── Live search ────────────────────────────────────────

    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search SharePoint via the Microsoft Search API."""
        await self._ensure_token()
        client = await self._get_client()

        search_url = f"{self.GRAPH_BASE}/search/query"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "requests": [
                {
                    "entityTypes": ["driveItem", "listItem"],
                    "query": {"queryString": query},
                    "from": 0,
                    "size": 10,
                }
            ]
        }

        response = await client.post(search_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        results = []
        for result_set in data.get("value", []):
            for hit_container in result_set.get("hitsContainers", []):
                for hit in hit_container.get("hits", []):
                    resource = hit.get("resource", {})
                    doc = Document(
                        id=f"sharepoint-search-{resource.get('id', '')}",
                        source="sharepoint",
                        title=resource.get("name", "Untitled"),
                        content=hit.get("summary", ""),
                        url=resource.get("webUrl", ""),
                        updated_at=datetime.now(timezone.utc),
                        metadata={"search_result": True},
                    )
                    results.append(doc)

        return results[:10]

    # ── Document conversion ────────────────────────────────

    async def _drive_item_to_document(self, item: dict, drive_name: str, path: str) -> Document | None:
        """Convert a drive item to a Document, downloading file content when possible."""
        try:
            name = item.get("name", "Untitled")
            item_id = item.get("id", "")

            # Only process text-based files
            mime = item.get("file", {}).get("mimeType", "")
            text_mimes = [
                "text/", "application/json", "application/xml",
                "application/vnd.openxmlformats",  # Office docs
                "application/pdf",
            ]
            if not any(mime.startswith(m) for m in text_mimes):
                return None

            web_url = item.get("webUrl", "")
            description = item.get("description", "")
            size = item.get("size", 0)

            updated_str = item.get("lastModifiedDateTime", "")
            updated_at = datetime.now(timezone.utc)
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            # Build metadata header
            content_parts = [f"**File:** {name}"]
            if path:
                content_parts.append(f"**Path:** {path}/{name}")
            content_parts.append(f"**Drive:** {drive_name}")
            content_parts.append(f"**Size:** {size:,} bytes")
            if description:
                content_parts.append(f"\n{description}")

            # Download actual file content for text-based files (cap at 10MB)
            file_content = ""
            if size <= 10_000_000:
                file_content = await self._download_file_content(item, mime)

            if file_content:
                content_parts.append("\n---\n")
                content_parts.append(file_content)

            return Document(
                id=f"sharepoint-{item_id}",
                source="sharepoint",
                title=name,
                content="\n".join(content_parts),
                url=web_url,
                updated_at=updated_at,
                metadata={"drive_name": drive_name, "path": path, "mime_type": mime},
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse SharePoint drive item: {e}")
            return None

    async def _download_file_content(self, item: dict, mime: str) -> str:
        """Download and extract text content from a SharePoint file."""
        try:
            download_url = item.get("@microsoft.graph.downloadUrl")
            if not download_url:
                # Fallback: use the content endpoint
                parent_ref = item.get("parentReference", {})
                drive_id = parent_ref.get("driveId", "")
                item_id = item.get("id", "")
                if not drive_id or not item_id:
                    return ""
                download_url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"

            await self._ensure_token()
            client = await self._get_client()
            headers = {"Authorization": f"Bearer {self._access_token}"}

            response = await client.get(download_url, headers=headers, follow_redirects=True)
            response.raise_for_status()

            # For plain text files, return content directly
            if mime.startswith("text/") or mime in ("application/json", "application/xml"):
                return response.text

            # For other supported types, return what we can decode
            try:
                return response.text
            except Exception:
                return ""

        except Exception as e:
            self.logger.debug(f"Could not download content for {item.get('name', '?')}: {e}")
            return ""

    def _list_item_to_document(self, item: dict, list_name: str) -> Document | None:
        """Convert a SharePoint list item to a Document."""
        try:
            item_id = item.get("id", "")
            fields = item.get("fields", {})

            title = fields.get("Title", fields.get("FileLeafRef", f"Item {item_id}"))

            # Build content from all fields
            content_parts = [f"**List:** {list_name}"]
            for key, value in fields.items():
                if key.startswith("@odata") or key.startswith("_"):
                    continue
                if value is not None and str(value).strip():
                    content_parts.append(f"**{key}:** {value}")

            web_url = item.get("webUrl", "")

            updated_str = item.get("lastModifiedDateTime", "")
            updated_at = datetime.now(timezone.utc)
            if updated_str:
                try:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            return Document(
                id=f"sharepoint-list-{list_name}-{item_id}",
                source="sharepoint",
                title=title,
                content="\n".join(content_parts),
                url=web_url,
                updated_at=updated_at,
                metadata={"list_name": list_name, "item_id": item_id},
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse SharePoint list item: {e}")
            return None

    # ── Checkpoint ─────────────────────────────────────────

    def get_checkpoint_state(self) -> dict:
        return dict(self._checkpoint)

    async def health_check(self) -> bool:
        try:
            await self._api_get(f"{self.GRAPH_BASE}/sites/{self.site_id}")
            return True
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
