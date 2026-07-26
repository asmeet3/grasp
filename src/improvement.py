"""Evaluation-gated self-improvement proposals; never direct mutation."""

from __future__ import annotations

from typing import Any

from .changesets import ChangeSetService
from .core.security import AuthContext, Permission, PolicyEngine
from .ingestion import IngestionCandidate


class ImprovementService:
    def __init__(
        self,
        change_sets: ChangeSetService,
        *,
        enabled: bool = False,
    ):
        self.change_sets = change_sets
        self.enabled = enabled
        self.policy = PolicyEngine()

    async def propose(
        self,
        context: AuthContext,
        candidate: IngestionCandidate,
        *,
        evaluation: dict[str, Any],
        approval_evidence: dict[str, Any],
    ) -> str:
        self.policy.require(context, Permission.REVIEW)
        if not self.enabled:
            raise RuntimeError("SELF_IMPROVEMENT_ENABLED is false")
        required = {
            "schema_passed",
            "regression_passed",
            "security_passed",
            "target_improved",
            "suite_not_degraded",
        }
        if not all(evaluation.get(key) is True for key in required):
            raise ValueError("Improvement proposal did not pass every evaluation gate")
        change_set = await self.change_sets.create(
            "self-improvement",
            organization_id=context.organization_id,
            creator_user_id=context.user_id,
            provenance={
                "evaluation": evaluation,
                "approval_evidence": approval_evidence,
                "activation": "canary_required",
            },
        )
        await self.change_sets.stage_candidate(change_set["id"], candidate)
        await self.change_sets.submit(change_set["id"])
        return change_set["id"]
