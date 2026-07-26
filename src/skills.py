"""Versioned skill manifests, validation, and allowlisted tool routing."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class SkillBudget(BaseModel):
    max_cost_units: int = Field(ge=0)
    timeout_seconds: int = Field(gt=0, le=3600)


class SkillManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    version: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    required_context: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_providers: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    approval_policy: str = "always"
    budget: SkillBudget
    idempotency: str
    evaluation_cases: list[dict[str, Any]] = Field(min_length=1)
    failure_behavior: str
    compensation_behavior: str = ""

    @model_validator(mode="after")
    def validate_write_approval(self):
        write_permissions = {"execute_actions", "propose_write"}
        if write_permissions.intersection(self.permissions) and self.approval_policy == "never":
            raise ValueError("write-capable skills must define an approval policy")
        return self


class SkillRouter:
    """Returns only the selected manifest and its permitted tools."""

    def __init__(self, manifests: list[SkillManifest]):
        self.manifests = {manifest.name: manifest for manifest in manifests}

    def select(self, requested_name: str | None, domain: str) -> SkillManifest | None:
        if requested_name:
            return self.manifests.get(requested_name)
        candidates = [item for item in self.manifests.values() if item.domain == domain]
        return (
            sorted(candidates, key=lambda item: (item.name, item.version))[-1]
            if candidates
            else None
        )

    @staticmethod
    def allowed_tool_definitions(
        manifest: SkillManifest,
        definitions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        allowed = set(manifest.allowed_tools)
        return [definition for definition in definitions if definition.get("name") in allowed]
