"""Tests for the StructuredMemoryService."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.security import (
    ROLE_PERMISSIONS,
    AuthContext,
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
        principals=frozenset({f"organization:{org}", "user:user-1", f"role:{role}"}),
    )


# ── Entity type validation ────────────────────────────────────


def test_entity_types_are_complete():
    expected = {
        "person", "team", "project", "product",
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

    with patch("src.deepseek_compat.AsyncDeepSeek", return_value=mock_client):
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


# ---- Extraction prompt quality ----


def test_extraction_prompt_is_recall_oriented():
    """The extraction prompt must favor coverage over precision filtering."""
    from src.memory import EXTRACTION_SYSTEM_PROMPT

    assert "Extract every entity" in EXTRACTION_SYSTEM_PROMPT
    assert "ownership/RACI tables" in EXTRACTION_SYSTEM_PROMPT
    # People/teams stay broad, but temporary work records must not become
    # project or product nodes - without hardcoding any company's key scheme.
    assert "do NOT treat individual work items" in EXTRACTION_SYSTEM_PROMPT
    assert "regardless of how those items are named or keyed" in EXTRACTION_SYSTEM_PROMPT
    assert "whatever their naming or key scheme" in EXTRACTION_SYSTEM_PROMPT
    assert "bug reports" in EXTRACTION_SYSTEM_PROMPT
    assert "meeting records" in EXTRACTION_SYSTEM_PROMPT
    assert "live/persistent product" in EXTRACTION_SYSTEM_PROMPT
    assert "when in doubt, leave it out" not in EXTRACTION_SYSTEM_PROMPT
    assert "single passing mention" not in EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_extraction_resolves_relationships_via_alias_and_case():
    """Relationship endpoints must resolve via aliases and different casing."""
    mock_extraction = {
        "entities": [
            {
                "entity_type": "person",
                "canonical_name": "Hana Kim",
                "aliases": ["Hana"],
                "attributes": {"role": "QA Owner"},
                "confidence": "high",
            },
            {
                "entity_type": "team",
                "canonical_name": "Route Planning",
                "aliases": [],
                "attributes": {},
                "confidence": "medium",
            },
        ],
        "relationships": [
            {
                "source": "hana",  # alias, different casing
                "relationship_type": "owns",
                "target": "route planning",  # canonical, different casing
                "confidence": "high",
            }
        ],
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(mock_extraction))]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    svc = StructuredMemoryService(
        engine=MagicMock(),
        policy=PolicyEngine(),
        anthropic_api_key="test-key",
    )

    entity_ids = {"Hana Kim": "entity-hana", "Route Planning": "entity-route"}
    added_relationships: list[dict] = []

    async def mock_upsert(context, values):
        return entity_ids[values["canonical_name"]]

    async def mock_add_rel(context, values):
        added_relationships.append(values)
        return str(uuid.uuid4())

    svc.upsert_entity = mock_upsert
    svc.add_relationship = mock_add_rel

    with patch("src.deepseek_compat.AsyncDeepSeek", return_value=mock_client):
        result = await svc.extract_entities_from_text(
            _context(),
            "Hana owns Route Planning.",
        )

    assert result["relationships_created"] == 1
    assert added_relationships[0]["source_entity_id"] == "entity-hana"
    assert added_relationships[0]["target_entity_id"] == "entity-route"


@pytest.mark.asyncio
async def test_extraction_chunks_long_documents():
    """Entities past the first 8k characters must still be extracted."""

    def build_extraction(content: str) -> dict:
        if "Zoe Winters" in content:
            return {
                "entities": [
                    {
                        "entity_type": "person",
                        "canonical_name": "Zoe Winters",
                        "aliases": [],
                        "attributes": {},
                        "confidence": "medium",
                    }
                ],
                "relationships": [],
            }
        return {
            "entities": [
                {
                    "entity_type": "person",
                    "canonical_name": "Alice Chen",
                    "aliases": [],
                    "attributes": {},
                    "confidence": "high",
                }
            ],
            "relationships": [],
        }

    async def fake_create(**kwargs):
        content = kwargs["messages"][0]["content"]
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=json.dumps(build_extraction(content)))
        ]
        return mock_response

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=fake_create)

    svc = StructuredMemoryService(
        engine=MagicMock(),
        policy=PolicyEngine(),
        anthropic_api_key="test-key",
    )

    upserted: list[dict] = []

    async def mock_upsert(context, values):
        upserted.append(values)
        return str(uuid.uuid4())

    svc.upsert_entity = mock_upsert
    svc.add_relationship = AsyncMock(return_value=str(uuid.uuid4()))

    # Alice appears at the start; Zoe only near the end, past 8k chars.
    text = "Alice Chen joined the project.\n\n" + (
        "filler content\n" * 600
    ) + "Zoe Winters owns delivery."
    assert len(text) > 8_000

    with patch("src.deepseek_compat.AsyncDeepSeek", return_value=mock_client):
        result = await svc.extract_entities_from_text(_context(), text)

    assert mock_client.messages.create.call_count >= 2
    names = {u["canonical_name"] for u in upserted}
    assert "Alice Chen" in names
    assert "Zoe Winters" in names
    assert result["entities_created"] == 2


@pytest.mark.asyncio
async def test_upsert_returns_persisted_id_on_conflict():
    """Dedup conflicts must return the existing row id, not a phantom UUID."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value="existing-id")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    svc = StructuredMemoryService(engine=mock_engine, policy=PolicyEngine())

    entity_id = await svc.upsert_entity(
        _context(),
        {
            "entity_type": "person",
            "canonical_name": "Alice Chen",
            "deduplication_key": "person:alice chen",
        },
    )
    assert entity_id == "existing-id"


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

    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

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
    from src.agent.tools import TOOL_DEFINITIONS, ToolExecutor

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
