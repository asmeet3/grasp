from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.changesets import ChangeSetService
from src.ingestion import IngestionCandidate


def candidate(**overrides) -> IngestionCandidate:
    values = {
        "source": "notion",
        "external_id": "1",
        "title": "Policy",
        "content": "Approved evidence",
        "content_hash": "abc",
        "updated_at": datetime.now(UTC),
        "acl_principals": ("organization:default",),
    }
    values.update(overrides)
    return IngestionCandidate(**values)


def test_changeset_validation_requires_acl() -> None:
    with pytest.raises(ValueError, match="ACL"):
        ChangeSetService._validate_candidate(candidate(acl_principals=()))


def test_file_operations_replace_same_target_deterministically() -> None:
    old = [{"op": "write", "path": "a.md", "content": "old"}]
    new = [{"op": "write", "path": "a.md", "content": "new"}]
    assert ChangeSetService._merge_operations(old, new) == new


def test_replay_detects_only_files_changed_since_proposal() -> None:
    class Repository:
        values = {
            ("policy.md", "base"): "old",
            ("policy.md", "current"): "newer",
            ("new.md", "base"): "",
            ("new.md", "current"): "",
        }

        def read_committed_file(self, path, commit):
            return self.values[(path, commit)]

    service = object.__new__(ChangeSetService)
    service.repository = Repository()
    row = {
        "base_commit_sha": "base",
        "operations": [
            {"op": "write", "path": "policy.md", "content": "proposal"},
            {"op": "write", "path": "new.md", "content": "new file"},
        ],
    }
    assert service._find_replay_conflicts(row, "current") == ["policy.md"]
