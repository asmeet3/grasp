"""DeepSeek compatibility shim for Anthropic SDK interface.

Wraps ``openai.AsyncOpenAI`` (pointed at DeepSeek's OpenAI-compatible endpoint)
and exposes the same surface that the rest of the codebase expects from
``anthropic.AsyncAnthropic``:

* ``client.messages.create(...)``
* ``client.messages.stream(...)``  (async context-manager)
  - ``async for text in stream.text_stream``
  - ``await stream.get_final_message()``

Tool definitions are converted from Anthropic format (``input_schema``) to
OpenAI format (``parameters``) on the fly, and responses are converted back.

Usage (drop-in replacement)::

    from src.deepseek_compat import AsyncDeepSeek   # instead of AsyncAnthropic
    client = AsyncDeepSeek(api_key="sk-...")

This module is the DeepSeek backend selected by ``src.llm.build_async_client``
when ``LLM_PROVIDER=deepseek``; with ``LLM_PROVIDER=anthropic`` the official
``anthropic`` SDK is used instead.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


# ---------------------------------------------------------------------------
# Response object shims
# ---------------------------------------------------------------------------


@dataclass
class _ContentBlock:
    """Mimics anthropic.types.ContentBlock (text or tool_use)."""

    type: str  # "text" | "tool_use"
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class _Message:
    """Mimics anthropic.types.Message."""

    content: list[_ContentBlock]
    stop_reason: str  # "end_turn" | "tool_use"
    model: str = ""
    id: str = ""


# ---------------------------------------------------------------------------
# Tool-definition translation helpers
# ---------------------------------------------------------------------------


def _to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool defs (``input_schema``) to OpenAI format (``parameters``)."""
    oai_tools = []
    for t in anthropic_tools:
        oai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return oai_tools


def _to_openai_messages(messages: list[dict], system: str | None) -> list[dict]:
    """Convert Anthropic-style messages + system prompt to flat OpenAI messages list.

    Anthropic quirks handled:
    - ``system`` is a top-level param; maps to ``{"role": "system", ...}`` prepended.
    - ``content`` can be a string *or* a list of blocks.
    - Tool-use blocks (``type: tool_use``) become ``tool_calls`` on the assistant message.
    - Tool-result blocks (``type: tool_result``) become ``role: tool`` messages.
    """
    oai_msgs: list[dict] = []
    if system:
        oai_msgs.append({"role": "system", "content": system})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            converted: dict[str, Any] = {"role": role, "content": content}
            if role == "assistant":
                # DeepSeek thinking mode requires every assistant message to
                # carry a reasoning_content field (empty is accepted when the
                # turn had no reasoning, e.g. stored plain-text history).
                converted["reasoning_content"] = (
                    msg.get("reasoning_content", "") if isinstance(msg, dict) else ""
                )
            oai_msgs.append(converted)
            continue

        # List of blocks
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if btype == "text":
                    txt = (
                        block.get("text", "")
                        if isinstance(block, dict)
                        else getattr(block, "text", "")
                    )
                    text_parts.append(txt)
                elif btype == "tool_use":
                    bid = (
                        block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                    )
                    bname = (
                        block.get("name", "")
                        if isinstance(block, dict)
                        else getattr(block, "name", "")
                    )
                    binput = (
                        block.get("input", {})
                        if isinstance(block, dict)
                        else getattr(block, "input", {})
                    )
                    tool_calls.append(
                        {
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": json.dumps(binput),
                            },
                        }
                    )
            oai_msg: dict[str, Any] = {
                "role": "assistant",
                # DeepSeek thinking mode expects an empty string (not null) on
                # assistant messages that carry tool calls, plus the
                # reasoning_content field on every assistant message.
                "content": " ".join(text_parts) or ("" if tool_calls else None),
                "reasoning_content": (
                    msg.get("reasoning_content", "") if isinstance(msg, dict) else ""
                ),
            }
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            oai_msgs.append(oai_msg)

        elif role == "user":
            # May contain tool_result blocks
            tool_results: list[dict] = []
            text_parts = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if btype == "tool_result":
                    tid = (
                        block.get("tool_use_id", "")
                        if isinstance(block, dict)
                        else getattr(block, "tool_use_id", "")
                    )
                    bcontent = (
                        block.get("content", "")
                        if isinstance(block, dict)
                        else getattr(block, "content", "")
                    )
                    tool_results.append({"role": "tool", "tool_call_id": tid, "content": bcontent})
                else:
                    txt = (
                        block.get("text", "")
                        if isinstance(block, dict)
                        else getattr(block, "text", "")
                    )
                    text_parts.append(txt)

            if tool_results:
                oai_msgs.extend(tool_results)
            if text_parts:
                oai_msgs.append({"role": "user", "content": " ".join(text_parts)})
            if not tool_results and not text_parts:
                oai_msgs.append({"role": "user", "content": ""})

    return oai_msgs


def _parse_response(oai_response) -> _Message:
    """Convert an OpenAI chat completion response to Anthropic-style _Message."""
    choice = oai_response.choices[0]
    oai_msg = choice.message
    finish_reason = choice.finish_reason  # "stop" | "tool_calls"

    content_blocks: list[_ContentBlock] = []

    if oai_msg.content is not None:
        content_blocks.append(_ContentBlock(type="text", text=oai_msg.content))

    if oai_msg.tool_calls:
        for tc in oai_msg.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            content_blocks.append(
                _ContentBlock(
                    type="tool_use",
                    id=tc.id,
                    name=tc.function.name,
                    input=arguments,
                )
            )

    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
    return _Message(
        content=content_blocks,
        stop_reason=stop_reason,
        model=oai_response.model,
        id=oai_response.id,
    )


# ---------------------------------------------------------------------------
# Streaming shim
# ---------------------------------------------------------------------------


class _StreamContext:
    """Async context manager that mimics Anthropic's stream context.

    Provides:
    - ``async for text in stream.text_stream``
    - ``await stream.get_final_message()``
    """

    def __init__(self, client: AsyncOpenAI, kwargs: dict):
        self._client = client
        self._kwargs = kwargs
        self._collected_text = ""
        self._final_message: _Message | None = None
        self._stream_done = False

    async def __aenter__(self) -> "_StreamContext":
        return self

    async def __aexit__(self, *args):
        pass

    @property
    def text_stream(self) -> AsyncGenerator[str, None]:
        return self._iter_text()

    async def _iter_text(self):
        """Stream text tokens and accumulate them for get_final_message()."""
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = "stop"
        full_content = ""

        # Use create(stream=True) instead of the chat.completions.stream() helper:
        # the helper auto-parses tool calls and rejects every non-strict function
        # tool ("Only strict function tools can be auto-parsed"), which DeepSeek's
        # OpenAI-compatible endpoint does not require. We accumulate tool calls
        # manually below, so the raw chunk stream is all we need.
        stream = await self._client.chat.completions.create(stream=True, **self._kwargs)
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            # Accumulate text
            if delta.content:
                full_content += delta.content
                self._collected_text += delta.content
                yield delta.content

            # Accumulate tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

        # Build final message
        content_blocks: list[_ContentBlock] = []
        if full_content:
            content_blocks.append(_ContentBlock(type="text", text=full_content))

        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            content_blocks.append(
                _ContentBlock(
                    type="tool_use",
                    id=tc["id"],
                    name=tc["name"],
                    input=arguments,
                )
            )

        stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
        self._final_message = _Message(
            content=content_blocks,
            stop_reason=stop_reason,
        )
        self._stream_done = True

    async def get_final_message(self) -> _Message:
        if not self._stream_done or self._final_message is None:
            # Drain the stream if not already consumed
            async for _ in self._iter_text():
                pass
        return self._final_message  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Messages namespace
# ---------------------------------------------------------------------------


class _Messages:
    def __init__(self, oai_client: AsyncOpenAI):
        self._client = oai_client

    def _build_kwargs(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> dict:
        oai_messages = _to_openai_messages(messages, system)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    async def create(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        **_extra,
    ) -> _Message:
        kwargs = self._build_kwargs(model, messages, max_tokens, system, tools, temperature)
        response = await self._client.chat.completions.create(**kwargs)
        return _parse_response(response)

    def stream(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        **_extra,
    ) -> _StreamContext:
        kwargs = self._build_kwargs(model, messages, max_tokens, system, tools, temperature)
        return _StreamContext(self._client, kwargs)


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class AsyncDeepSeek:
    """Drop-in replacement for ``anthropic.AsyncAnthropic`` using DeepSeek's API."""

    def __init__(self, api_key: str, base_url: str = DEEPSEEK_BASE_URL, **kwargs):
        self._oai = AsyncOpenAI(api_key=api_key, base_url=base_url, **kwargs)
        self.messages = _Messages(self._oai)
