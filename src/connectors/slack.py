"""Slack connector — retrieves messages from Slack via Web API.

Uses cursor-based pagination, fetches channel history and thread replies,
and uses search.messages for live queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

import httpx

from .base import BaseConnector, Document

logger = logging.getLogger(__name__)


class SlackConnector(BaseConnector):
    """Connector for Slack via the Web API."""

    API_BASE = "https://slack.com/api"

    def __init__(self, bot_token: str, batch_size: int = 50):
        super().__init__("slack")
        self.bot_token = bot_token
        self.batch_size = batch_size
        self._checkpoint: dict = {}
        self._channel_cache: dict[str, str] = {}  # id -> name
        self._user_cache: dict[str, str] = {}      # id -> display_name
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def _api_get(self, method: str, params: dict | None = None) -> dict:
        client = await self._get_client()
        url = f"{self.API_BASE}/{method}"
        response = await self.rate_limiter.execute(client, "GET", url, params=params)
        data = response.json()
        if not data.get("ok", False):
            error = data.get("error", "unknown_error")
            raise RuntimeError(f"Slack API error on {method}: {error}")
        return data

    async def _get_user_name(self, user_id: str) -> str:
        """Resolve a user ID to a display name, with caching."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            data = await self._api_get("users.info", {"user": user_id})
            user = data.get("user", {})
            name = user.get("real_name") or user.get("name", user_id)
            self._user_cache[user_id] = name
            return name
        except Exception:
            self._user_cache[user_id] = user_id
            return user_id

    # ── Full retrieval ─────────────────────────────────────

    async def full_retrieve(self, checkpoint: dict | None = None) -> AsyncGenerator[list[Document], None]:
        """Retrieve all messages from all accessible channels."""
        processed_channels: set[str] = set()
        resume_channel: str | None = None
        resume_oldest: str | None = None

        if checkpoint:
            processed_channels = set(checkpoint.get("processed_channels", []))
            resume_channel = checkpoint.get("current_channel")
            resume_oldest = checkpoint.get("oldest_ts")

        channels = await self._get_all_channels()
        self.logger.info(f"Found {len(channels)} channels to process")

        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel.get("name", channel_id)
            self._channel_cache[channel_id] = channel_name

            if channel_id in processed_channels and channel_id != resume_channel:
                continue

            oldest = resume_oldest if channel_id == resume_channel else "0"
            resume_oldest = None

            self.logger.info(f"Processing channel: #{channel_name}")

            async for batch in self._get_channel_history(channel_id, channel_name, oldest=oldest):
                yield batch

            processed_channels.add(channel_id)
            self._checkpoint = {
                "processed_channels": list(processed_channels),
                "current_channel": None,
                "oldest_ts": None,
            }

        self.logger.info("Full Slack retrieval complete")

    async def _get_all_channels(self) -> list[dict]:
        """Fetch all accessible channels."""
        channels = []
        cursor = None

        while True:
            params = {"types": "public_channel,private_channel", "limit": 200}
            if cursor:
                params["cursor"] = cursor

            data = await self._api_get("conversations.list", params)
            channels.extend(data.get("channels", []))

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return channels

    async def _get_channel_history(
        self,
        channel_id: str,
        channel_name: str,
        oldest: str = "0",
        latest: str | None = None,
    ) -> AsyncGenerator[list[Document], None]:
        """Fetch all messages in a channel, including thread replies."""
        cursor = None
        messages_by_date: dict[str, list[dict]] = {}

        while True:
            params: dict = {"channel": channel_id, "limit": 200, "oldest": oldest}
            if latest:
                params["latest"] = latest
            if cursor:
                params["cursor"] = cursor

            data = await self._api_get("conversations.history", params)

            for msg in data.get("messages", []):
                ts = float(msg.get("ts", 0))
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

                # Fetch thread replies if this is a parent message
                if msg.get("reply_count", 0) > 0:
                    thread_ts = msg.get("ts")
                    replies = await self._get_thread_replies(channel_id, thread_ts)
                    msg["_thread_replies"] = replies

                messages_by_date.setdefault(date_str, []).append(msg)

            # Update checkpoint
            if data.get("messages"):
                last_ts = data["messages"][-1].get("ts", "0")
                self._checkpoint = {
                    "current_channel": channel_id,
                    "oldest_ts": last_ts,
                }

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        # Convert messages grouped by date into documents
        batch: list[Document] = []
        for date_str, msgs in sorted(messages_by_date.items()):
            doc = await self._messages_to_document(msgs, channel_id, channel_name, date_str)
            if doc:
                batch.append(doc)
                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []

        if batch:
            yield batch

    async def _get_thread_replies(self, channel_id: str, thread_ts: str) -> list[dict]:
        """Fetch all replies in a thread."""
        replies = []
        cursor = None

        while True:
            params: dict = {"channel": channel_id, "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor

            data = await self._api_get("conversations.replies", params)
            # Skip the first message (it's the parent)
            thread_msgs = data.get("messages", [])
            if thread_msgs:
                replies.extend(thread_msgs[1:])  # Skip parent

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return replies

    # ── Incremental retrieval ──────────────────────────────

    async def incremental_retrieve(self, since: datetime) -> AsyncGenerator[list[Document], None]:
        """Retrieve messages posted since the given timestamp."""
        oldest = str(since.timestamp())
        channels = await self._get_all_channels()

        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel.get("name", channel_id)
            self._channel_cache[channel_id] = channel_name

            async for batch in self._get_channel_history(channel_id, channel_name, oldest=oldest):
                yield batch

    # ── Live search ────────────────────────────────────────

    async def live_search(self, query: str, hours: int = 4) -> list[Document]:
        """Search Slack for recent messages matching the query."""
        try:
            data = await self._api_get("search.messages", {
                "query": query,
                "sort": "timestamp",
                "sort_dir": "desc",
                "count": 10,
            })

            results = []
            matches = data.get("messages", {}).get("matches", [])

            for match in matches[:10]:
                ts = float(match.get("ts", 0))
                msg_time = datetime.fromtimestamp(ts, tz=timezone.utc)

                channel = match.get("channel", {})
                channel_name = channel.get("name", "unknown") if isinstance(channel, dict) else "unknown"
                user_name = match.get("username", match.get("user", "unknown"))

                doc = Document(
                    id=f"slack-search-{match.get('ts', '')}",
                    source="slack",
                    title=f"#{channel_name} — {user_name}",
                    content=match.get("text", ""),
                    url=match.get("permalink", ""),
                    updated_at=msg_time,
                    metadata={"channel_name": channel_name, "search_result": True},
                )
                results.append(doc)

            return results
        except Exception as e:
            self.logger.warning(f"Slack search failed: {e}")
            return []

    # ── Document conversion ────────────────────────────────

    async def _messages_to_document(
        self, messages: list[dict], channel_id: str, channel_name: str, date_str: str
    ) -> Document | None:
        """Convert a day's worth of messages into a single Document."""
        if not messages:
            return None

        parts = []
        for msg in sorted(messages, key=lambda m: float(m.get("ts", 0))):
            ts = float(msg.get("ts", 0))
            time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
            user_id = msg.get("user", "unknown")
            user_name = await self._get_user_name(user_id) if user_id != "unknown" else "unknown"
            text = msg.get("text", "")

            parts.append(f"**[{time_str}] {user_name}:** {text}")

            # Include thread replies
            for reply in msg.get("_thread_replies", []):
                reply_ts = float(reply.get("ts", 0))
                reply_time = datetime.fromtimestamp(reply_ts, tz=timezone.utc).strftime("%H:%M")
                reply_user = await self._get_user_name(reply.get("user", "unknown"))
                reply_text = reply.get("text", "")
                parts.append(f"  ↳ **[{reply_time}] {reply_user}:** {reply_text}")

        content = "\n\n".join(parts)

        return Document(
            id=f"slack-{channel_id}-{date_str}",
            source="slack",
            title=f"#{channel_name} — {date_str}",
            content=content,
            url=f"https://app.slack.com/client/{channel_id}",
            updated_at=datetime.now(timezone.utc),
            metadata={"channel_id": channel_id, "channel_name": channel_name, "date": date_str},
        )

    # ── Checkpoint ─────────────────────────────────────────

    def get_checkpoint_state(self) -> dict:
        return dict(self._checkpoint)

    async def health_check(self) -> bool:
        try:
            await self._api_get("auth.test")
            return True
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
