"""Typed, ACL-governed organizational memory access.

Provides entity extraction from document text via the configured LLM,
entity/relationship CRUD with ACL enforcement, graph traversal,
search, review workflow, work-item lifecycle, and aggregate statistics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from . import llm
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
You are an entity extraction system for an organizational knowledge base (Slack, Confluence, SharePoint, Jira, etc.), feeding a knowledge graph. Recover the people, teams, projects, and products in the text so the graph reflects the organization.

Entity types:
- person: any named individual mentioned in the text (full name, or an unambiguous first/last name or handle)
- team: any named organizational group or area of ownership (e.g. Platform Team, Data Team, Dispatch, Route Planning)
- project: any named, ongoing initiative, program, or workstream with a stable identity (epic, program, release initiative, migration). Exclude temporary work records - do NOT treat individual work items (tickets, bugs, stories, tasks, sprint items) or meeting records as projects, regardless of how those items are named or keyed.
- product: any named live/persistent system, service, or tool, internal or external (e.g. LumaFleet, Dispatch Dashboard, ETA service). Exclude temporary items - do NOT treat meetings, work-item tickets, bug reports, or documents as products.

Extract every entity that is named and substantively present. People and teams count even in org charts, ownership/RACI tables, team rosters, meeting notes, and review documents. Presence in a list, table, or header is enough when the entity is real and named.

For projects and products, only extract stable, ongoing entities that exist beyond a single work item or event: a real project or program with a name, or a live/persistent product. Do not let meetings, agendas, sprint reviews/retros, bug reports, user stories, or individual work items (tickets/stories/tasks from any tracking system, whatever their naming or key scheme) become project or product nodes - they are temporary, high-churn records, not entities in the knowledge graph.

Skip only content that is not about a real entity: pure boilerplate (revision metadata, system messages), placeholder text, undiscussed watcher/CC lists, and unresolved generic references (e.g. "the team", "the customer"). Do not invent names, attributes, or relationships - everything must be grounded in the text.

Merge repeated mentions of an entity into one entry with all aliases; don't duplicate.

For each entity, provide:
- entity_type: person, team, project, or product
- canonical_name: most formal/complete name
- aliases: alternate names, abbreviations, or handles seen in the text
- attributes: key-value pairs stated in the text (e.g. role, parent org, status, owner) - never invented
- confidence: high/medium/low

Relationships:
- Extract a relationship whenever it is explicitly stated or clearly implied - e.g. "X leads Y", "X reports to Y", "X owns Y", "X is a member of Y", "X is blocked by Y", "X depends on Y", "X is part of Y", "X uses Y", or a table row assigning an owner/backup to an area.
- relationship_type: one of "owns", "leads", "member_of", "depends_on", "uses", "decided_by", "blocked_by", "related_to", "reports_to", "part_of"
- source and target must match a canonical name or alias from your entity list; prefer canonical names.
- confidence: high/medium/low

Return ONLY a JSON object with this schema, no markdown or explanation:
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

If nothing qualifies, return {"entities": [], "relationships": []}.
"""

EXTRACTION_MAX_CHARS_PER_CHUNK = 8_000
EXTRACTION_CHUNK_OVERLAP = 500
EXTRACTION_MAX_TOKENS = 4096


def _normalize_name(name: object) -> str:
    """Normalize an entity name for alias-insensitive matching."""
    return " ".join(str(name).strip().lower().split())


def _chunk_text(
    text: str,
    max_chars: int = EXTRACTION_MAX_CHARS_PER_CHUNK,
    overlap: int = EXTRACTION_CHUNK_OVERLAP,
) -> list[str]:
    """Split long documents into overlapping chunks at paragraph boundaries."""
    text = text or ""
    if len(text) <= max_chars:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start + max_chars // 2, end)
            if boundary == -1:
                boundary = text.rfind("\n", start + max_chars // 2, end)
            if boundary != -1 and boundary > start:
                end = boundary + 1
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _parse_extraction_json(raw_text: str) -> dict:
    """Robustly parse the model's JSON, tolerating fences and preambles."""
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        first_newline = raw_text.find("\n")
        if first_newline != -1:
            raw_text = raw_text[first_newline + 1 :]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].rstrip()
    if "{" in raw_text and "}" in raw_text:
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        raw_text = raw_text[start:end]
    if not raw_text.strip():
        return {"entities": [], "relationships": []}
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("Extraction JSON is not an object")
    return parsed


class StructuredMemoryService:
    def __init__(
        self,
        engine: AsyncEngine,
        policy: PolicyEngine | None = None,
        anthropic_api_key: str = "",
        classifier_model: str = "claude-haiku-4-5-20251001",
        llm_provider: str = "anthropic",
    ):
        self.engine = engine
        self.policy = policy or PolicyEngine()
        self.anthropic_api_key = anthropic_api_key
        self.classifier_model = classifier_model
        self.llm_provider = llm_provider

    # ── Extraction ────────────────────────────────────────────────

    async def extract_entities_from_text(
        self,
        context: AuthContext,
        text_content: str,
        *,
        source_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract entities and relationships from text via the configured LLM.

        Returns the raw extraction result plus counts of upserted items.
        """
        if not self.anthropic_api_key:
            return {"entities_created": 0, "relationships_created": 0, "error": "No API key"}

        client = llm.build_async_client(self.llm_provider, self.anthropic_api_key)

        # Process long documents in overlapping chunks so nothing past the
        # first 8k characters is silently dropped.
        extractions: list[dict] = []
        for chunk in _chunk_text(text_content):
            try:
                response = await client.messages.create(
                    model=self.classifier_model,
                    max_tokens=EXTRACTION_MAX_TOKENS,
                    system=EXTRACTION_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": chunk}],
                )
                if not response.content:
                    raise ValueError("LLM returned empty response content")
                raw_text = response.content[0].text.strip()
                extractions.append(_parse_extraction_json(raw_text))
            except Exception as exc:
                logger.warning("Entity extraction failed for a chunk: %s", exc)

        evidence = source_evidence or {}
        evidence_list = [evidence] if evidence else []

        # Upsert extracted entities
        name_to_id: dict[str, str] = {}
        canonical_to_id: dict[str, str] = {}
        entities_created = 0
        for extraction in extractions:
            for entity in extraction.get("entities") or []:
                entity_type = str(entity.get("entity_type", "")).lower()
                if entity_type not in ENTITY_TYPES:
                    continue
                canonical = str(entity.get("canonical_name", "")).strip()
                if not canonical:
                    continue
                normalized = _normalize_name(canonical)
                dedup_key = f"{entity_type}:{normalized}"
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
                name_to_id.setdefault(normalized, entity_id)
                canonical_to_id.setdefault(normalized, entity_id)
                for alias in entity.get("aliases") or []:
                    alias_normalized = _normalize_name(alias)
                    if alias_normalized:
                        name_to_id.setdefault(alias_normalized, entity_id)
                entities_created += 1

        # Create relationships
        relationships_created = 0
        seen_relationships: set[tuple[str, str, str]] = set()
        for extraction in extractions:
            for rel in extraction.get("relationships") or []:
                source_id = self._resolve_entity_id(
                    name_to_id, canonical_to_id, rel.get("source", "")
                )
                target_id = self._resolve_entity_id(
                    name_to_id, canonical_to_id, rel.get("target", "")
                )
                if not source_id or not target_id:
                    continue
                rel_type = str(rel.get("relationship_type", "related_to")).strip() or "related_to"
                rel_key = (source_id, rel_type, target_id)
                if rel_key in seen_relationships:
                    continue
                seen_relationships.add(rel_key)
                try:
                    await self.add_relationship(
                        context,
                        {
                            "source_entity_id": source_id,
                            "relationship_type": rel_type,
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
            "extraction": {
                "entities": [
                    entity
                    for extraction in extractions
                    for entity in extraction.get("entities") or []
                ],
                "relationships": [
                    rel
                    for extraction in extractions
                    for rel in extraction.get("relationships") or []
                ],
            },
        }

    @staticmethod
    def _resolve_entity_id(
        name_to_id: dict[str, str],
        canonical_to_id: dict[str, str],
        raw_name: object,
    ) -> str | None:
        """Resolve a relationship endpoint to an entity id.

        Tries an exact (case/whitespace-insensitive) match first, then a
        containment match against canonical names so short references like
        "Hana" still resolve to "Hana Kim" when unambiguous.
        """
        normalized = _normalize_name(raw_name)
        if not normalized:
            return None
        if normalized in name_to_id:
            return name_to_id[normalized]
        matches = {
            entity_id
            for key, entity_id in canonical_to_id.items()
            if normalized in key or key in normalized
        }
        if len(matches) == 1:
            return next(iter(matches))
        return None

    # ── Rebuild ──────────────────────────────────────────────────────────

    async def rebuild_all_entities(
        self,
        context: AuthContext,
        repo_path: Path,
    ) -> dict[str, Any]:
        """Delete all entities & relationships, then re-extract from every knowledge doc.

        This is a destructive operation: all existing entity and relationship
        rows for the organisation are removed before extraction begins.
        """
        org = context.organization_id

        # 1. Delete all relationships then entities for this org
        async with self.engine.begin() as conn:
            del_rels = await conn.execute(
                delete(entity_relationships_table).where(
                    entity_relationships_table.c.organization_id == org
                )
            )
            deleted_relationships = del_rels.rowcount

            del_ents = await conn.execute(
                delete(entities_table).where(entities_table.c.organization_id == org)
            )
            deleted_entities = del_ents.rowcount

        logger.info(
            "Rebuild: purged %d entities and %d relationships for org %s",
            deleted_entities,
            deleted_relationships,
            org,
        )

        # 2. Collect all knowledge .md files from the repo
        knowledge_dir = repo_path / "knowledge"
        md_files: list[Path] = []
        if knowledge_dir.exists():
            md_files = [
                f for f in knowledge_dir.rglob("*.md") if f.name != "README.md" and f.is_file()
            ]

        docs_processed = 0
        total_entities_created = 0
        total_relationships_created = 0

        for md_path in md_files:
            try:
                content = await asyncio.to_thread(md_path.read_text, encoding="utf-8")
            except Exception as exc:
                logger.warning("Rebuild: cannot read %s: %s", md_path, exc)
                continue

            if len(content) < 50:
                continue

            relative = md_path.relative_to(repo_path).as_posix()
            result = await self.extract_entities_from_text(
                context,
                content,
                source_evidence={"source": "rebuild", "path": relative},
            )
            docs_processed += 1
            total_entities_created += result.get("entities_created", 0)
            total_relationships_created += result.get("relationships_created", 0)

        logger.info(
            "Rebuild complete: processed %d docs, created %d entities, %d relationships",
            docs_processed,
            total_entities_created,
            total_relationships_created,
        )

        return {
            "deleted_entities": deleted_entities,
            "deleted_relationships": deleted_relationships,
            "docs_processed": docs_processed,
            "entities_created": total_entities_created,
            "relationships_created": total_relationships_created,
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

    async def get_entity(self, context: AuthContext, entity_id: str) -> dict[str, Any] | None:
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
            .returning(entities_table.c.id)
        )
        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            persisted_id = result.scalar_one_or_none()
        # On a dedup conflict the generated id never hit the database; return
        # the id of the row that actually exists so relationships stay valid.
        return persisted_id or entity_id

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
                (
                    await conn.execute(
                        select(work_items_table).where(
                            work_items_table.c.id == item_id,
                            work_items_table.c.organization_id == context.organization_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise ValueError("Work item not found")
            current = row["status"]
            allowed = valid_transitions.get(current, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Cannot transition from '{current}' to '{new_status}'. Allowed: {allowed}"
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
