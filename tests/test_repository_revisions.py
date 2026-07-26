from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.connectors.base import Document
from src.repo.manager import RepoManager


def manager(tmp_path) -> RepoManager:
    return RepoManager(tmp_path / "knowledge", anthropic_api_key="test")


def test_plan_is_isolated_until_commit(tmp_path) -> None:
    repository = manager(tmp_path)
    document = Document(
        id="doc-1",
        source="notion",
        title="Pricing decision",
        content="The approved price is 10.",
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        metadata={
            "acl_principals": ["organization:default"],
            "domain": "finance",
        },
    )
    operations = repository.plan_document(document, "decisions")
    knowledge_op = next(op for op in operations if op["path"].startswith("knowledge/"))
    assert not (repository.repo_path / knowledge_op["path"]).exists()

    commit = repository.apply_operations_and_commit(
        operations, "knowledge: test proposal", repository.current_commit()
    )
    assert len(commit) == 40
    assert repository.read_committed_file(knowledge_op["path"])
    assert 'acl_principals: ["organization:default"]' in knowledge_op["content"]


def test_stale_proposal_cannot_overwrite_newer_commit(tmp_path) -> None:
    repository = manager(tmp_path)
    stale_base = repository.current_commit()
    first = Document(
        id="doc-1",
        source="notion",
        title="One",
        content="one",
        metadata={"acl_principals": ["organization:default"]},
    )
    repository.apply_operations_and_commit(
        repository.plan_document(first, "topics"), "first", stale_base
    )
    second = Document(
        id="doc-2",
        source="notion",
        title="Two",
        content="two",
        metadata={"acl_principals": ["organization:default"]},
    )
    with pytest.raises(RuntimeError, match="base_commit_conflict"):
        repository.apply_operations_and_commit(
            repository.plan_document(second, "topics"), "stale", stale_base
        )
