"""LLM client factory.

Selects the async Anthropic-style client used across the app based on
``LLM_PROVIDER``:

- ``anthropic`` - the official ``anthropic`` SDK.
- ``deepseek`` - the OpenAI-compatible DeepSeek shim, which mirrors the
  Anthropic client interface.

Both providers share the same key field (``ANTHROPIC_API_KEY``), so call
sites keep a single API-key parameter.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from .deepseek_compat import AsyncDeepSeek

SUPPORTED_PROVIDERS = ("anthropic", "deepseek")


def build_async_client(provider: str | None, api_key: str) -> Any:
    """Return the async LLM client for ``provider`` using ``api_key``."""
    normalized = (provider or "anthropic").strip().lower()
    if normalized == "anthropic":
        return AsyncAnthropic(api_key=api_key)
    if normalized == "deepseek":
        return AsyncDeepSeek(api_key=api_key)
    raise ValueError(
        f"Unsupported LLM_PROVIDER={provider!r}; expected one of {SUPPORTED_PROVIDERS}"
    )
