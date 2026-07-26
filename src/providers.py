"""Capability-based live context providers and selective routing."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .connectors.base import BaseConnector, Document
from .core.security import AuthContext, PolicyEngine


class ProviderCapability(str, enum.Enum):
    SEARCH = "search"
    BROWSE = "browse"
    READ = "read"
    PROPOSE_WRITE = "propose_write"
    EXECUTE_ACTION = "execute_action"
    ACL_DISCOVERY = "acl_discovery"
    INCREMENTAL_SYNC = "incremental_sync"


class ConnectorProvider:
    """Read-only compatibility adapter for the existing connectors."""

    def __init__(self, connector: BaseConnector, policy: PolicyEngine | None = None):
        self.connector = connector
        self.name = connector.name
        self.policy = policy or PolicyEngine()
        self._capabilities = frozenset(
            {
                ProviderCapability.SEARCH.value,
                ProviderCapability.READ.value,
                ProviderCapability.INCREMENTAL_SYNC.value,
            }
        )

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    async def search(self, query: str, context: AuthContext) -> list[Document]:
        documents = await self.connector.live_search(query)
        return [doc for doc in documents if self.policy.can_access_document(context, doc.metadata)]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    providers: tuple[ConnectorProvider, ...]
    reason: str


class ProviderRouter:
    SOURCE_TERMS = {
        "confluence": {"confluence", "wiki"},
        "jira": {"jira", "issue", "ticket", "sprint"},
        "sharepoint": {"sharepoint", "onedrive"},
        "slack": {"slack", "channel", "thread"},
        "notion": {"notion"},
    }

    def __init__(self, providers: list[ConnectorProvider], max_providers: int = 2):
        self.providers = {provider.name: provider for provider in providers}
        self.max_providers = max_providers

    def select(self, query: str, *, freshness_required: bool = False) -> ProviderSelection:
        lowered = query.lower()
        scores: list[tuple[int, str]] = []
        for name in self.providers:
            score = sum(term in lowered for term in self.SOURCE_TERMS.get(name, set()))
            if score:
                scores.append((score, name))
        if not scores and not freshness_required:
            return ProviderSelection((), "committed knowledge is preferred")
        if not scores:
            scores = [(1, name) for name in self.providers]
        scores.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(self.providers[name] for _, name in scores[: self.max_providers])
        return ProviderSelection(selected, "source relevance and freshness")
