"""Typed, ACL-governed organizational memory access.

Provides entity extraction from document text (via Claude Haiku),
entity/relationship CRUD with ACL enforcement, graph traversal,
search, review workflow, work-item lifecycle, and aggregate statistics.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .core.security import AuthContext, PolicyEngine
from .database import entities_table, entity_relationships_table, work_items_table

logger = logging.getLogger(__name__)

ENTITY_TYPES = frozenset(
    {
        "person",
        "team",
        "project",
        "product",
    }
)

EXTRACTION_SYSTEM_PROMPT = """\
You are an entity extraction system for an organizational knowledge base.
Extract structured entities and relationships from the provided text.

Entity types: person, team, project, product

For each entity, provide:
- entity_type: one of the types above
- canonical_name: the most formal/complete name
- aliases: list of alternative names or abbreviations
- attributes: key-value pairs of notable attributes
- confidence: "high", "medium", or "low"

For each relationship, provide:
- source: canonical name of the source entity
- relationship_type: one of "owns", "leads", "member_of", "depends_on", \
"uses", "decided_by", "blocked_by", "related_to", "reports_to", "part_of"
- target: canonical name of the target entity
- confidence: "high", "medium", or "low"

Return ONLY a JSON object with this schema (no markdown, no explanation):
{
  "entities": [
    {
      "entity_type": "person",
      "canonical_name": "Jane Smith",
      "aliases": ["J. Smith"],
      "attributes": {"role": "Engineering Manager"},
      "confidence": "high"
    }
  ],
  "relationships": [
    {
      "source": "Jane Smith",
      "relationship_type": "leads",
      "target": "Platform Team",
      "confidence": "high"
    }
  ]
}

If no entities or relationships can be extracted, return {"entities": [], "relationships": []}.
"""


class StructuredMemoryService:
    def __init__(
        self,
        engine: AsyncEngine,
        policy: PolicyEngine | None = None,
        anthropic_api_key: str = "",
        classifier_model: str = "claude-haiku-4-5-20251001",
    ):
        self.engine = engine
        self.policy = policy or PolicyEngine()
        self.anthropic_api_key = anthropic_api_key
        self.classifier_model = classifier_model

    # ── Extraction ────────────────────────────────────────────────

    async def extract_entities_from_text(
        self,
        context: AuthContext,
        text_content: str,
        *,
        source_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract entities and relationships from text via Claude Haiku.

        Returns the raw extraction result plus counts of upserted items.
        """
        if not self.anthropic_api_key:
            return {"entities_created": 0, "relationships_created": 0, "error": "No API key"}

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.anthropic_api_key)

        # Truncate to ~8k chars to keep Haiku calls cheap
        truncated = text_content[:8_000]

        try:
            response = await client.messages.create(
                model=self.classifier_model,
                max_tokens=2048,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": truncated}],
            )
            raw_text = response.content[0].text.strip()
            
            # Robust JSON extraction to handle markdown and preambles
            if "{" in raw_text and "}" in raw_text:
                start = raw_text.find("{")
                end = raw_text.rfind("}") + 1
                raw_text = raw_text[start:end]
            
            extraction = json.loads(raw_text)
        except Exception as exc:
            logger.warning("Entity extraction failed: %s", exc)
            return {"entities_created": 0, "relationships_created": 0, "error": str(exc)}

        evidence = source_evidence or {}
        evidence_list = [evidence] if evidence else []

        # Upsert extracted entities
        name_to_id: dict[str, str] = {}
        entities_created = 0
        for entity in extraction.get("entities") or []:
            entity_type = str(entity.get("entity_type", "")).lower()
            if entity_type not in ENTITY_TYPES:
                continue
            canonical = str(entity.get("canonical_name", "")).strip()
            if not canonical:
                continue
            dedup_key = f"{entity_type}:{canonical.lower()}"
            entity_id = await self.upsert_entity(
                context,
                {
                    "entity_type": entity_type,
                    "canonical_name": canonical,
                    "aliases": entity.get("aliases") or [],
                    "deduplication_key": dedup_key,
                    "attributes": entity.get("attributes") or {},
                    "evidence": evidence_list,
                    "confidence": entity.get("confidence", "medium"),
                },
            )
            name_to_id[canonical] = entity_id
            entities_created += 1

        # Create relationships
        relationships_created = 0
        for rel in extraction.get("relationships") or []:
            source_name = str(rel.get("source", "")).strip()
            target_name = str(rel.get("target", "")).strip()
            source_id = name_to_id.get(source_name)
            target_id = name_to_id.get(target_name)
            if not source_id or not target_id:
                continue
            try:
                await self.add_relationship(
                    context,
                    {
                        "source_entity_id": source_id,
                        "relationship_type": rel.get("relationship_type", "related_to"),
                        "target_entity_id": target_id,
                        "evidence": evidence_list,
                        "confidence": rel.get("confidence", "medium"),
                    },
                )
                relationships_created += 1
            except Exception as exc:
                logger.debug("Relationship creation skipped: %s", exc)

        return {
            "entities_created": entities_created,
            "relationships_created": relationships_created,
            "extraction": extraction,
        }

    # ── Entity CRUD ───────────────────────────────────────────────

    async def find_entities(
        self, context: AuthContext, *, entity_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        stmt = select(entities_table).where(
            entities_table.c.organization_id == context.organization_id,
            entities_table.c.valid_to.is_(None),
        )
        if entity_type:
            stmt = stmt.where(entities_table.c.entity_type == entity_type)
        stmt = stmt.order_by(entities_table.c.canonical_name)
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt.limit(min(limit, 200)))).mappings().all()
        return [
            dict(row)
            for row in rows
            if self.policy.can_access_principals(context, row["acl_principals"])
        ]

    async def get_entity(
        self, context: AuthContext, entity_id: str
    ) -> dict[str, Any] | None:
        stmt = select(entities_table).where(entities_table.c.id == entity_id)
        async with self.engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if not row:
            return None
        row_dict = dict(row)
        if not self.policy.can_access_principals(context, row_dict.get("acl_principals")):
            return None
        return row_dict

    async def search_entities(
        self,
        context: AuthContext,
        query: str,
        *,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Text search on canonical_name and aliases using ILIKE."""
        pattern = f"%{query}%"
        stmt = select(entities_table).where(
            entities_table.c.organization_id == context.organization_id,
            entities_table.c.valid_to.is_(None),
            (
                entities_table.c.canonical_name.ilike(pattern)
                | entities_table.c.aliases.cast(text("TEXT")).ilike(pattern)
            ),
        )
        if entity_type and entity_type in ENTITY_TYPES:
            stmt = stmt.where(entities_table.c.entity_type == entity_type)
        stmt = stmt.order_by(entities_table.c.canonical_name).limit(min(limit, 100))
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            dict(row)
            for row in rows
            if self.policy.can_access_principals(context, row["acl_principals"])
        ]

    async def upsert_entity(self, context: AuthContext, values: dict[str, Any]) -> str:
        """Typed upsert only; arbitrary SQL is never accepted."""
        entity_id = str(values.get("id") or uuid.uuid4())
        allowed = {
            "entity_type",
            "canonical_name",
            "aliases",
            "deduplication_key",
            "attributes",
            "evidence",
            "confidence",
            "sensitivity",
            "acl_principals",
            "valid_from",
            "valid_to",
        }
        data = {key: value for key, value in values.items() if key in allowed}
        data.update(id=entity_id, organization_id=context.organization_id)
        if not data.get("acl_principals"):
            data["acl_principals"] = [f"organization:{context.organization_id}"]
        stmt = (
            pg_insert(entities_table)
            .values(**data)
            .on_conflict_do_update(
                constraint="uq_entity_dedup",
                set_={
                    key: value
                    for key, value in data.items()
                    if key not in {"id", "organization_id"}
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return entity_id

    async def review_entity(
        self,
        context: AuthContext,
        entity_id: str,
        action: str,
        *,
        merge_target_id: str | None = None,
    ) -> dict[str, Any]:
        """Review an entity: confirm, retire, or merge.

        - ``confirm`` sets confidence to "high".
        - ``retire`` sets ``valid_to`` to now, removing the entity from active queries.
        - ``merge`` retires the entity and redirects its relationships to the merge target.
        """
        entity = await self.get_entity(context, entity_id)
        if not entity:
            raise ValueError("Entity not found or access denied")

        if action == "confirm":
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(entities_table)
                    .where(entities_table.c.id == entity_id)
                    .values(confidence="high")
                )
            return {"id": entity_id, "action": "confirmed"}

        elif action == "retire":
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(entities_table)
                    .where(entities_table.c.id == entity_id)
                    .values(valid_to=datetime.now(UTC))
                )
            return {"id": entity_id, "action": "retired"}

        elif action == "merge":
            if not merge_target_id:
                raise ValueError("merge_target_id is required for merge action")
            target = await self.get_entity(context, merge_target_id)
            if not target:
                raise ValueError("Merge target not found or access denied")
            async with self.engine.begin() as conn:
                # Re-point relationships from source to target
                await conn.execute(
                    update(entity_relationships_table)
                    .where(entity_relationships_table.c.source_entity_id == entity_id)
                    .values(source_entity_id=merge_target_id)
                )
                await conn.execute(
                    update(entity_relationships_table)
                    .where(entity_relationships_table.c.target_entity_id == entity_id)
                    .values(target_entity_id=merge_target_id)
                )
                # Retire the merged entity
                await conn.execute(
                    update(entities_table)
                    .where(entities_table.c.id == entity_id)
                    .values(valid_to=datetime.now(UTC))
                )
            return {"id": entity_id, "action": "merged", "merged_into": merge_target_id}

        raise ValueError(f"Unknown review action: {action}")

    # ── Relationships ─────────────────────────────────────────────

    async def add_relationship(self, context: AuthContext, values: dict[str, Any]) -> str:
        relationship_id = str(uuid.uuid4())
        allowed = {
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "evidence",
            "confidence",
            "valid_from",
            "valid_to",
        }
        data = {key: value for key, value in values.items() if key in allowed}
        data.update(id=relationship_id, organization_id=context.organization_id)
        async with self.engine.begin() as conn:
            await conn.execute(entity_relationships_table.insert().values(**data))
        return relationship_id

    async def find_relationships(
        self, context: AuthContext, entity_id: str
    ) -> list[dict[str, Any]]:
        """Return all active relationships involving the given entity."""
        stmt = select(entity_relationships_table).where(
            entity_relationships_table.c.organization_id == context.organization_id,
            entity_relationships_table.c.valid_to.is_(None),
            (
                (entity_relationships_table.c.source_entity_id == entity_id)
                | (entity_relationships_table.c.target_entity_id == entity_id)
            ),
        )
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    async def get_entity_graph(
        self,
        context: AuthContext,
        entity_id: str,
        *,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Return the entity and its connected neighborhood at the given depth."""
        entity = await self.get_entity(context, entity_id)
        if not entity:
            raise ValueError("Entity not found or access denied")

        visited_ids: set[str] = {entity_id}
        all_entities: list[dict[str, Any]] = [entity]
        all_relationships: list[dict[str, Any]] = []
        frontier: set[str] = {entity_id}

        for _ in range(min(depth, 3)):  # cap at depth 3
            if not frontier:
                break
            next_frontier: set[str] = set()
            for fid in frontier:
                rels = await self.find_relationships(context, fid)
                for rel in rels:
                    all_relationships.append(rel)
                    for key in ("source_entity_id", "target_entity_id"):
                        neighbor_id = rel[key]
                        if neighbor_id not in visited_ids:
                            neighbor = await self.get_entity(context, neighbor_id)
                            if neighbor:
                                all_entities.append(neighbor)
                                visited_ids.add(neighbor_id)
                                next_frontier.add(neighbor_id)
            frontier = next_frontier

        return {
            "root_entity": entity,
            "entities": all_entities,
            "relationships": all_relationships,
        }

    async def get_full_graph(
        self,
        context: AuthContext,
        *,
        entity_types: list[str] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return all active entities and relationships for the graph view."""
        stmt = select(entities_table).where(
            entities_table.c.organization_id == context.organization_id,
            entities_table.c.valid_to.is_(None),
        )
        if entity_types:
            valid = [t for t in entity_types if t in ENTITY_TYPES]
            if valid:
                stmt = stmt.where(entities_table.c.entity_type.in_(valid))
        stmt = stmt.order_by(entities_table.c.canonical_name).limit(min(limit, 500))

        async with self.engine.begin() as conn:
            entity_rows = (await conn.execute(stmt)).mappings().all()

        entities = [
            dict(row)
            for row in entity_rows
            if self.policy.can_access_principals(context, row["acl_principals"])
        ]
        entity_ids = {e["id"] for e in entities}

        # Fetch relationships where both ends are in the visible entity set
        rel_stmt = select(entity_relationships_table).where(
            entity_relationships_table.c.organization_id == context.organization_id,
            entity_relationships_table.c.valid_to.is_(None),
            entity_relationships_table.c.source_entity_id.in_(entity_ids),
            entity_relationships_table.c.target_entity_id.in_(entity_ids),
        )
        async with self.engine.begin() as conn:
            rel_rows = (await conn.execute(rel_stmt)).mappings().all()

        relationships = [dict(row) for row in rel_rows]

        return {
            "nodes": entities,
            "edges": relationships,
        }

    # ── Work Items ────────────────────────────────────────────────

    async def propose_work_item(self, context: AuthContext, values: dict[str, Any]) -> str:
        item_id = str(uuid.uuid4())
        data = {
            "id": item_id,
            "organization_id": context.organization_id,
            "title": values["title"],
            "evidence": values.get("evidence", []),
            "owner_user_id": values.get("owner_user_id"),
            "due_at": values.get("due_at"),
            "confidence": values.get("confidence", "low"),
            "status": "proposed",
            "deduplication_key": values["deduplication_key"],
            "origin": values.get("origin", {"user_id": context.user_id}),
        }
        async with self.engine.begin() as conn:
            await conn.execute(
                pg_insert(work_items_table)
                .values(**data)
                .on_conflict_do_nothing(constraint="uq_work_item_dedup")
            )
        return item_id

    async def list_work_items(
        self,
        context: AuthContext,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        stmt = select(work_items_table).where(
            work_items_table.c.organization_id == context.organization_id,
        )
        if status:
            stmt = stmt.where(work_items_table.c.status == status)
        stmt = stmt.order_by(work_items_table.c.title).limit(min(limit, 200))
        async with self.engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    async def update_work_item_status(
        self,
        context: AuthContext,
        item_id: str,
        new_status: str,
    ) -> dict[str, Any]:
        """Transition a work item's status.

        Valid transitions: proposed → accepted, proposed → dismissed,
        accepted → completed, accepted → dismissed.
        """
        valid_transitions = {
            "proposed": {"accepted", "dismissed"},
            "accepted": {"completed", "dismissed"},
        }
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    select(work_items_table).where(
                        work_items_table.c.id == item_id,
                        work_items_table.c.organization_id == context.organization_id,
                    )
                )
            ).mappings().first()
            if not row:
                raise ValueError("Work item not found")
            current = row["status"]
            allowed = valid_transitions.get(current, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Cannot transition from '{current}' to '{new_status}'. "
                    f"Allowed: {allowed}"
                )
            await conn.execute(
                update(work_items_table)
                .where(work_items_table.c.id == item_id)
                .values(status=new_status)
            )
        return {"id": item_id, "status": new_status}

    # ── Statistics ────────────────────────────────────────────────

    async def get_memory_stats(self, context: AuthContext) -> dict[str, Any]:
        """Aggregate counts for the admin dashboard."""
        org = context.organization_id
        async with self.engine.begin() as conn:
            # Entity counts by type
            entity_rows = (
                await conn.execute(
                    select(
                        entities_table.c.entity_type,
                        func.count().label("count"),
                    )
                    .where(
                        entities_table.c.organization_id == org,
                        entities_table.c.valid_to.is_(None),
                    )
                    .group_by(entities_table.c.entity_type)
                )
            ).all()
            by_type = {row[0]: row[1] for row in entity_rows}

            # Total active entities
            total_entities = sum(by_type.values())

            # Relationship count
            rel_count_row = (
                await conn.execute(
                    select(func.count()).where(
                        entity_relationships_table.c.organization_id == org,
                        entity_relationships_table.c.valid_to.is_(None),
                    )
                )
            ).scalar()

            # Work-item counts by status
            work_rows = (
                await conn.execute(
                    select(
                        work_items_table.c.status,
                        func.count().label("count"),
                    )
                    .where(work_items_table.c.organization_id == org)
                    .group_by(work_items_table.c.status)
                )
            ).all()
            work_by_status = {row[0]: row[1] for row in work_rows}

        return {
            "total_entities": total_entities,
            "entities_by_type": by_type,
            "total_relationships": rel_count_row or 0,
            "work_items_by_status": work_by_status,
            "total_work_items": sum(work_by_status.values()),
        }
