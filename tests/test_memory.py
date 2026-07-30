"""Tests for the StructuredMemoryService."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.security import (
    ROLE_PERMISSIONS,
    AuthContext,
    Permission,
    PolicyEngine,
    SystemRole,
)
from src.memory import ENTITY_TYPES, StructuredMemoryService


def _context(
    role: str = "administrator", org: str = "acme"
) -> AuthContext:
    role_enum = SystemRole(role)
    return AuthContext(
        user_id="user-1",
        organization_id=org,
        system_role=role_enum,
        permissions=ROLE_PERMISSIONS[role_enum],
        principals=frozenset({f"organization:{org}", f"user:user-1", f"role:{role}"}),
    )


# ── Entity type validation ────────────────────────────────────


def test_entity_types_are_complete():
    expected = {
        "person", "team", "project", "product",
        "process", "technology", "decision", "milestone",
    }
    assert ENTITY_TYPES == expected


# ── Extraction prompt parsing ────────────────────────────────


@pytest.mark.asyncio
async def test_extraction_returns_error_when_no_api_key():
    svc = StructuredMemoryService(
        engine=MagicMock(),
        policy=PolicyEngine(),
        anthropic_api_key="",
    )
    result = await svc.extract_entities_from_text(
        _context(), "Some text about a person named Alice in team Platform."
    )
    assert result["entities_created"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_extraction_parses_claude_response():
    """Verify the extraction pipeline correctly parses a mock LLM response."""
    mock_extraction = {
        "entities": [
            {
                "entity_type": "person",
                "canonical_name": "Alice Chen",
                "aliases": ["Alice"],
                "attributes": {"role": "Tech Lead"},
                "confidence": "high",
            },
            {
                "entity_type": "team",
                "canonical_name": "Platform Team",
                "aliases": [],
                "attributes": {},
                "confidence": "medium",
            },
        ],
        "relationships": [
            {
                "source": "Alice Chen",
                "relationship_type": "leads",
                "target": "Platform Team",
                "confidence": "high",
            }
        ],
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(mock_extraction))]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    # Track upserted entities and added relationships
    upserted_entities: list[dict] = []
    added_relationships: list[dict] = []

    svc = StructuredMemoryService(
        engine=MagicMock(),
        policy=PolicyEngine(),
        anthropic_api_key="test-key",
    )

    async def mock_upsert(context, values):
        entity_id = str(uuid.uuid4())
        upserted_entities.append(values)
        return entity_id

    async def mock_add_rel(context, values):
        rel_id = str(uuid.uuid4())
        added_relationships.append(values)
        return rel_id

    svc.upsert_entity = mock_upsert
    svc.add_relationship = mock_add_rel

    with patch("src.memory.AsyncAnthropic", return_value=mock_client):
        result = await svc.extract_entities_from_text(
            _context(),
            "Alice Chen leads the Platform Team and is responsible for infrastructure.",
        )

    assert result["entities_created"] == 2
    assert result["relationships_created"] == 1
    assert len(upserted_entities) == 2
    assert upserted_entities[0]["canonical_name"] == "Alice Chen"
    assert upserted_entities[1]["canonical_name"] == "Platform Team"
    assert added_relationships[0]["relationship_type"] == "leads"


# ── Entity types filtering ───────────────────────────────────


def test_unknown_entity_type_excluded_from_entity_types():
    assert "unknown" not in ENTITY_TYPES
    assert "person" in ENTITY_TYPES


# ── Work item lifecycle ──────────────────────────────────────


@pytest.mark.asyncio
async def test_work_item_status_transition_validation():
    """Verify the state machine rejects invalid transitions."""
    svc = StructuredMemoryService(engine=MagicMock(), policy=PolicyEngine())

    # Mock DB returning a work item with status 'proposed'
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "id": "item-1",
        "status": "proposed",
        "organization_id": "acme",
    }[k]
    mock_mapping = MagicMock()
    mock_mapping.first.return_value = mock_row

    mock_result = MagicMock()
    mock_result.mappings.return_value = mock_mapping

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = AsyncMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock()

    svc.engine = mock_engine

    # proposed -> completed should be rejected
    with pytest.raises(ValueError, match="Cannot transition"):
        await svc.update_work_item_status(
            _context(), "item-1", "completed"
        )


# ── Review actions ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_entity_unknown_action_raises():
    """Unknown review actions should raise ValueError."""
    svc = StructuredMemoryService(engine=MagicMock(), policy=PolicyEngine())

    # Mock get_entity to return a valid entity
    svc.get_entity = AsyncMock(return_value={
        "id": "ent-1",
        "entity_type": "person",
        "canonical_name": "Test",
        "acl_principals": ["organization:acme"],
    })

    with pytest.raises(ValueError, match="Unknown review action"):
        await svc.review_entity(_context(), "ent-1", "invalid_action")


@pytest.mark.asyncio
async def test_review_entity_not_found_raises():
    """Reviewing a non-existent entity should raise ValueError."""
    svc = StructuredMemoryService(engine=MagicMock(), policy=PolicyEngine())
    svc.get_entity = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Entity not found"):
        await svc.review_entity(_context(), "missing-id", "confirm")


# ── Merge requires target ────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_requires_target_id():
    """Merge action must specify merge_target_id."""
    svc = StructuredMemoryService(engine=MagicMock(), policy=PolicyEngine())
    svc.get_entity = AsyncMock(return_value={
        "id": "ent-1",
        "entity_type": "person",
        "canonical_name": "Test",
        "acl_principals": ["organization:acme"],
    })

    with pytest.raises(ValueError, match="merge_target_id is required"):
        await svc.review_entity(_context(), "ent-1", "merge")


# ── Tool integration ────────────────────────────────────────


def test_tool_definitions_include_memory_when_service_present():
    """ToolExecutor.tool_definitions should include search_memory when memory_service is set."""
    from src.agent.tools import MEMORY_TOOL_DEFINITION, TOOL_DEFINITIONS, ToolExecutor

    executor = ToolExecutor(
        dispatcher=MagicMock(),
        vector_store=MagicMock(),
        repo_manager=MagicMock(),
        connectors={},
        memory_service=MagicMock(),
    )
    defs = executor.tool_definitions
    names = [t["name"] for t in defs]
    assert "search_memory" in names
    assert len(defs) == len(TOOL_DEFINITIONS) + 1


def test_tool_definitions_exclude_memory_when_service_absent():
    """ToolExecutor.tool_definitions should not include search_memory without memory_service."""
    from src.agent.tools import TOOL_DEFINITIONS, ToolExecutor

    executor = ToolExecutor(
        dispatcher=MagicMock(),
        vector_store=MagicMock(),
        repo_manager=MagicMock(),
        connectors={},
        memory_service=None,
    )
    defs = executor.tool_definitions
    names = [t["name"] for t in defs]
    assert "search_memory" not in names
    assert len(defs) == len(TOOL_DEFINITIONS)


# ── Search memory tool dispatch ──────────────────────────────


@pytest.mark.asyncio
async def test_search_memory_tool_returns_results():
    """The search_memory tool should format results from StructuredMemoryService."""
    from src.agent.tools import ToolExecutor

    mock_memory = AsyncMock()
    mock_memory.search_entities = AsyncMock(return_value=[
        {
            "id": "ent-1",
            "entity_type": "person",
            "canonical_name": "Alice Chen",
            "confidence": "high",
            "aliases": ["Alice"],
            "attributes": {"role": "Tech Lead"},
        }
    ])
    mock_memory.find_relationships = AsyncMock(return_value=[])

    executor = ToolExecutor(
        dispatcher=MagicMock(),
        vector_store=MagicMock(),
        repo_manager=MagicMock(),
        connectors={},
        memory_service=mock_memory,
    )

    result = await executor.execute(
        "search_memory",
        {"query": "Alice"},
        auth_context=_context(),
    )

    assert "Alice Chen" in result
    assert "person" in result
    assert "Tech Lead" in result


@pytest.mark.asyncio
async def test_search_memory_tool_disabled():
    """search_memory should return disabled message when no memory_service."""
    from src.agent.tools import ToolExecutor

    executor = ToolExecutor(
        dispatcher=MagicMock(),
        vector_store=MagicMock(),
        repo_manager=MagicMock(),
        connectors={},
        memory_service=None,
    )

    result = await executor.execute(
        "search_memory",
        {"query": "anything"},
        auth_context=_context(),
    )

    assert "not enabled" in result.lower()
