"""Narrow architectural boundaries used by the composition root.

The protocols deliberately describe capabilities instead of concrete storage
technology.  Git, Chroma, PostgreSQL, and connector implementations remain
replaceable adapters and no domain service needs to import them directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .security import AuthContext


@runtime_checkable
class KnowledgeRepository(Protocol):
    @property
    def repo_path(self) -> Path: ...

    def current_commit(self) -> str: ...

    def read_committed_file(self, file_path: str, commit_sha: str | None = None) -> str: ...

    def apply_operations_and_commit(
        self,
        operations: Sequence[Mapping[str, Any]],
        message: str,
        expected_base_commit: str,
    ) -> str: ...


@runtime_checkable
class SearchIndex(Protocol):
    def search(
        self,
        query: str,
        n_results: int = 20,
        *,
        auth_context: AuthContext,
        source_filter: str | None = None,
        info_type_filter: str | None = None,
    ) -> list[Any]: ...

    def index_committed_document(
        self,
        document: Any,
        info_type: str,
        *,
        commit_sha: str,
        content_hash: str,
        organization_id: str,
    ) -> int: ...


@runtime_checkable
class ContextProvider(Protocol):
    name: str

    @property
    def capabilities(self) -> frozenset[str]: ...

    async def search(self, query: str, context: AuthContext) -> Sequence[Any]: ...


@runtime_checkable
class ChangeSetStore(Protocol):
    async def create(self, values: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def get(
        self, change_set_id: str, *, for_update: bool = False
    ) -> Mapping[str, Any] | None: ...

    async def update(self, change_set_id: str, values: Mapping[str, Any]) -> None: ...


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> str: ...

    async def run_forever(self) -> None: ...


@runtime_checkable
class AuditStore(Protocol):
    async def record(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        organization_id: str | None,
        resource_type: str,
        resource_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> None: ...
