"""Pure change-management invariants shared by persistence adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable, Mapping


class ChangeSetState(StrEnum):
    DRAFT = "draft"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    APPLYING = "applying"
    ACTIVE = "active"
    REJECTED = "rejected"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[ChangeSetState, frozenset[ChangeSetState]] = {
    ChangeSetState.DRAFT: frozenset({ChangeSetState.AWAITING_REVIEW, ChangeSetState.REJECTED}),
    ChangeSetState.AWAITING_REVIEW: frozenset(
        {ChangeSetState.APPROVED, ChangeSetState.APPLYING, ChangeSetState.REJECTED}
    ),
    ChangeSetState.APPROVED: frozenset({ChangeSetState.APPLYING, ChangeSetState.REJECTED}),
    ChangeSetState.APPLYING: frozenset({ChangeSetState.ACTIVE, ChangeSetState.FAILED}),
    ChangeSetState.FAILED: frozenset({ChangeSetState.APPLYING, ChangeSetState.REJECTED}),
    ChangeSetState.ACTIVE: frozenset(),
    ChangeSetState.REJECTED: frozenset(),
}


class ChangeSetConflict(RuntimeError):
    pass


def require_transition(current: str, target: str) -> None:
    try:
        current_state = ChangeSetState(current)
        target_state = ChangeSetState(target)
    except ValueError as exc:
        raise ChangeSetConflict("Unknown change-set state") from exc
    if target_state not in ALLOWED_TRANSITIONS[current_state]:
        raise ChangeSetConflict(f"Invalid change-set transition: {current} -> {target}")


def require_current_base(expected: str, current: str) -> None:
    if not expected or expected != current:
        raise ChangeSetConflict(
            f"base_commit_conflict: expected {expected or '<none>'}, current {current or '<none>'}"
        )


def summarize_operations(operations: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    summary = {"writes": 0, "deletes": 0}
    for operation in operations:
        kind = operation.get("op")
        if kind == "write":
            summary["writes"] += 1
        elif kind == "delete":
            summary["deletes"] += 1
        else:
            raise ChangeSetConflict(f"Unsupported operation: {kind}")
    summary["total"] = summary["writes"] + summary["deletes"]
    return summary
