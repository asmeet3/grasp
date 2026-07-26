from __future__ import annotations

import pytest

from src.core.changes import (
    ChangeSetConflict,
    require_current_base,
    require_transition,
    summarize_operations,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "awaiting_review"),
        ("awaiting_review", "applying"),
        ("applying", "active"),
        ("applying", "failed"),
        ("failed", "applying"),
        ("awaiting_review", "rejected"),
    ],
)
def test_valid_change_set_transitions(current: str, target: str) -> None:
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [("draft", "active"), ("active", "draft"), ("rejected", "applying")],
)
def test_invalid_change_set_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(ChangeSetConflict):
        require_transition(current, target)


def test_unknown_state_is_rejected() -> None:
    with pytest.raises(ChangeSetConflict, match="Unknown"):
        require_transition("made_up", "active")


def test_base_revision_must_match() -> None:
    require_current_base("abc", "abc")
    with pytest.raises(ChangeSetConflict, match="base_commit_conflict"):
        require_current_base("abc", "def")
    with pytest.raises(ChangeSetConflict):
        require_current_base("", "def")


def test_operation_summary_rejects_unknown_operations() -> None:
    assert summarize_operations([{"op": "write"}, {"op": "write"}, {"op": "delete"}]) == {
        "writes": 2,
        "deletes": 1,
        "total": 3,
    }
    with pytest.raises(ChangeSetConflict, match="Unsupported"):
        summarize_operations([{"op": "chmod"}])
