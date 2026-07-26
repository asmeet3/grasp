from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents import AgentDefinition, AgentService


def definition(**overrides) -> AgentDefinition:
    values = {
        "name": "Weekly risk brief",
        "role": "Delivery intelligence analyst",
        "owner_user_id": "user-1",
        "instructions": "Find delivery risks and cite the supporting company evidence.",
        "domains": ["engineering", "product"],
        "skills": ["risk_watch"],
        "allowed_classifications": ["internal"],
        "event_triggers": ["manual"],
    }
    values.update(overrides)
    return AgentDefinition(**values)


def test_agent_definition_accepts_manual_and_scheduled_routines() -> None:
    manual = definition()
    scheduled = definition(schedule="0 9 * * 1-5", event_triggers=["manual", "schedule"])

    assert manual.schedule is None
    assert scheduled.schedule == "0 9 * * 1-5"
    event_driven = definition(event_triggers=["manual", "knowledge_sync"])
    assert "knowledge_sync" in event_driven.event_triggers


@pytest.mark.parametrize("schedule", ["every day", "0 9 *", "61 * * * *"])
def test_agent_definition_rejects_invalid_cron(schedule: str) -> None:
    with pytest.raises(ValidationError):
        definition(schedule=schedule, event_triggers=["schedule"])


def test_agent_definition_rejects_unknown_skills_and_autonomous_actions() -> None:
    with pytest.raises(ValidationError, match="unsupported agent skills"):
        definition(skills=["invent_strategy"])
    with pytest.raises(ValidationError, match="read-only"):
        definition(allowed_actions=["create_jira_issue"])


def test_agent_prompt_contains_governed_scope_and_safety_boundary() -> None:
    prompt = AgentService._build_prompt(definition(), "Focus on this week's changes.")

    assert "Assigned domains: engineering, product" in prompt
    assert "Allowed classifications: internal" in prompt
    assert "risk_watch" in prompt
    assert "Do not execute actions" in prompt
