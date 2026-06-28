"""Repository manager — Git-backed knowledge storage with three-layer structure.

Documents are organized in the company-brain layout:
  sources/   — raw ingestion (append-only, immutable after write)
  knowledge/ — structured, curated knowledge units
  _index/    — auto-generated retrieval layer (graph, tags, embeddings, freshness)
  _schema/   — frontmatter schemas and source-connector configs
  teams/     — team-scoped spaces

Classification via Claude Haiku maps documents into knowledge types.
Supports human-approved commits with remote push.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from git import Repo, InvalidGitRepositoryError

from ..connectors.base import Document, sanitize_filename

logger = logging.getLogger(__name__)

# ── Knowledge type taxonomy (maps to knowledge/ subdirectories) ────────

KNOWLEDGE_TYPES = [
    "decisions",   # ADRs, meeting notes, RFCs, design reviews
    "projects",    # Feature specs, PRDs, user stories, project overviews
    "processes",   # Runbooks, SOPs, deployment guides, test plans, QA
    "products",    # Product areas, their history, roadmaps
    "people",      # Expertise profiles (opt-in)
    "topics",      # Cross-cutting themes (architecture, strategy, incidents, etc.)
]

# Mapping from old info_types to new knowledge structure
OLD_TYPE_TO_NEW = {
    "architecture": "topics",
    "features":     "projects",
    "operations":   "processes",
    "testing":      "processes",
    "decisions":    "decisions",
    "strategy":     "products",
    "incidents":    "topics",
    "discussions":  "topics",
    "references":   "topics",
    "general":      "topics",
}

# Sub-topic mappings (for topics/ subdirectories)
OLD_TYPE_TO_SUBTOPIC = {
    "architecture": "architecture",
    "incidents":    "incidents",
    "discussions":  "discussions",
    "references":   "references",
    "general":      "general",
}

CLASSIFICATION_PROMPT = """You are a document classifier for a company knowledge base. Classify the following document into exactly ONE of these categories based on its title and content:

Categories:
- decisions: ADRs, meeting notes, RFCs, design reviews, decision records, retrospectives
- projects: Feature specs, PRDs, user stories, epics, project overviews, feature development
- processes: Runbooks, SOPs, deployment guides, operations, test plans, QA documentation, bug reports
- products: Product areas, roadmaps, strategy, OKRs, planning documents, vision statements
- people: Expertise profiles, team member information, skills inventories
- topics: Cross-cutting themes — architecture, system design, infrastructure, incident reports, postmortems, discussions, conversations, general documentation, wikis, guides, onboarding, reference materials

Document Title: {title}
Document Source: {source}
Content Preview (first 500 chars): {preview}

Respond with ONLY the category name, nothing else."""


class RepoManager:
    """Manages the Git-backed knowledge repository with company-brain structure."""

    def __init__(
        self,
        repo_path: Path,
        anthropic_api_key: str,
        classifier_model: str = "claude-haiku-4-5-20251001",
        remote_url: str = "",
        github_pat: str = "",
    ):
        self.repo_path = repo_path
        self.anthropic_client = AsyncAnthropic(api_key=anthropic_api_key)
        self.classifier_model = classifier_model
        self.remote_url = remote_url
        self.github_pat = github_pat
        self.state_dir = repo_path / ".grasp_state"
        self._repo: Repo | None = None

        self._init_repo()

    def _init_repo(self):
        """Initialize or open the Git repository with company-brain structure."""
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Create the three-layer directory structure
        # 1. _index/ — retrieval layer (auto-generated)
        index_dir = self.repo_path / "_index"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "embeddings").mkdir(exist_ok=True)

        # Initialize index files if they don't exist
        for index_file, default in [
            ("graph.json", {"nodes": [], "edges": [], "metadata": {"last_rebuilt": None}}),
            ("tags.json", {"taxonomy": {}, "metadata": {"last_rebuilt": None}}),
            ("people.json", {"experts": {}, "metadata": {"last_rebuilt": None}}),
            ("freshness.json", {"registry": {}, "metadata": {"last_rebuilt": None}}),
        ]:
            path = index_dir / index_file
            if not path.exists():
                path.write_text(json.dumps(default, indent=2), encoding="utf-8")

        # 2. _schema/ — modularity layer
        schema_dir = self.repo_path / "_schema"
        schema_dir.mkdir(exist_ok=True)
        connectors_dir = schema_dir / "source-connectors"
        connectors_dir.mkdir(exist_ok=True)
        self._init_schemas(schema_dir, connectors_dir)

        # 3. sources/ — raw ingestion (append-only)
        sources_dir = self.repo_path / "sources"
        sources_dir.mkdir(exist_ok=True)
        for source_name in ["confluence", "jira", "slack", "meetings", "emails"]:
            (sources_dir / source_name).mkdir(exist_ok=True)
        docs_dir = sources_dir / "docs"
        docs_dir.mkdir(exist_ok=True)
        for doc_source in ["notion", "sharepoint", "gdrive"]:
            (docs_dir / doc_source).mkdir(exist_ok=True)

        # 4. knowledge/ — curated knowledge
        knowledge_dir = self.repo_path / "knowledge"
        knowledge_dir.mkdir(exist_ok=True)
        for knowledge_type in KNOWLEDGE_TYPES:
            type_dir = knowledge_dir / knowledge_type
            type_dir.mkdir(exist_ok=True)
            # Create README.md index for each knowledge type if it doesn't exist
            readme = type_dir / "README.md"
            if not readme.exists():
                readme.write_text(
                    f"# {knowledge_type.title()}\n\n"
                    f"Index of all {knowledge_type} in the knowledge base.\n",
                    encoding="utf-8",
                )

        # Create topics subdirectories
        topics_dir = knowledge_dir / "topics"
        for subtopic in ["architecture", "incidents", "discussions", "references", "general", "security", "infrastructure"]:
            sub_dir = topics_dir / subtopic
            sub_dir.mkdir(exist_ok=True)
            readme = sub_dir / "README.md"
            if not readme.exists():
                readme.write_text(
                    f"# {subtopic.title()}\n\n"
                    f"Summary and links to all related knowledge about {subtopic}.\n",
                    encoding="utf-8",
                )

        # 5. teams/ — team-scoped spaces
        teams_dir = self.repo_path / "teams"
        teams_dir.mkdir(exist_ok=True)

        # Initialize Git repo
        try:
            self._repo = Repo(self.repo_path)
        except InvalidGitRepositoryError:
            self._repo = Repo.init(self.repo_path)
            # Create initial .gitignore to protect internal state
            gitignore = self.repo_path / ".gitignore"
            gitignore.write_text(
                "# Grasp internal state — do not commit\n"
                ".grasp_state/\n",
                encoding="utf-8",
            )
            # Create initial README
            readme = self.repo_path / "README.md"
            readme.write_text(
                "# Company Knowledge Repository\n\n"
                "This repository is automatically maintained by **Grasp** — "
                "an agentic AI institutional brain.\n\n"
                "## Structure\n\n"
                "- `_index/` — Retrieval layer (auto-generated, never hand-edited)\n"
                "- `_schema/` — Frontmatter schemas and source-connector configs\n"
                "- `sources/` — Raw ingestion (append-only, immutable after write)\n"
                "- `knowledge/` — Structured, curated knowledge units\n"
                "- `teams/` — Team-scoped spaces\n",
                encoding="utf-8",
            )
            self._repo.index.add(["README.md", ".gitignore"])
            self._repo.index.commit("Initial repository setup")
            logger.info(f"Initialized new Git repository at {self.repo_path}")

        # Configure remote if provided
        if self.remote_url:
            self._configure_remote()

    def _init_schemas(self, schema_dir: Path, connectors_dir: Path):
        """Create default schema YAML files if they don't exist."""
        schemas = {
            "decision.yaml": (
                "# Schema for decision knowledge units\n"
                "type: decision\n"
                "required_fields:\n"
                "  - id\n"
                "  - type\n"
                "  - title\n"
                "  - date\n"
                "  - status\n"
                "optional_fields:\n"
                "  - supersedes\n"
                "  - superseded_by\n"
                "  - tags\n"
                "  - stakeholders\n"
                "  - owner\n"
                "  - sources\n"
                "  - related\n"
                "  - freshness_check\n"
                "  - confidence\n"
                "statuses: [active, superseded, draft, archived]\n"
                "confidence_levels: [high, medium, low, contested]\n"
            ),
            "project.yaml": (
                "# Schema for project knowledge units\n"
                "type: project\n"
                "required_fields:\n"
                "  - id\n"
                "  - type\n"
                "  - title\n"
                "  - date\n"
                "  - status\n"
                "optional_fields:\n"
                "  - tags\n"
                "  - owner\n"
                "  - sources\n"
                "  - related\n"
                "  - freshness_check\n"
                "  - confidence\n"
                "statuses: [active, superseded, draft, archived]\n"
            ),
            "person.yaml": (
                "# Schema for expertise profiles\n"
                "type: person\n"
                "required_fields:\n"
                "  - id\n"
                "  - type\n"
                "  - title\n"
                "  - name\n"
                "optional_fields:\n"
                "  - expertise\n"
                "  - team\n"
                "  - contact\n"
            ),
        }

        for filename, content in schemas.items():
            path = schema_dir / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")

        # Source connector configs
        connector_configs = {
            "confluence.yaml": "# Confluence connector config\nsource: confluence\nbase_url: \"\"\n",
            "jira.yaml": "# Jira connector config\nsource: jira\nbase_url: \"\"\n",
            "slack.yaml": "# Slack connector config\nsource: slack\nbot_token: \"\"\n",
            "notion.yaml": "# Notion connector config\nsource: notion\napi_key: \"\"\n",
            "sharepoint.yaml": "# SharePoint connector config\nsource: sharepoint\ntenant_id: \"\"\n",
        }

        for filename, content in connector_configs.items():
            path = connectors_dir / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _configure_remote(self):
        """Set up the remote with PAT authentication."""
        try:
            # Inject PAT into URL for HTTPS remotes
            remote_url = self.remote_url
            if self.github_pat and remote_url.startswith("https://"):
                # https://github.com/... -> https://<PAT>@github.com/...
                remote_url = remote_url.replace("https://", f"https://{self.github_pat}@")

            try:
                origin = self._repo.remote("origin")
                # Update URL if it changed
                with origin.config_writer as cw:
                    cw.set("url", remote_url)
            except ValueError:
                self._repo.create_remote("origin", remote_url)

            logger.info("Git remote configured")
        except Exception as e:
            logger.warning(f"Failed to configure remote: {e}")

    # ── Classification ─────────────────────────────────────

    async def classify_document(self, doc: Document) -> str:
        """Classify a document into a knowledge type using Claude Haiku."""
        try:
            preview = doc.content[:500] if doc.content else ""
            prompt = CLASSIFICATION_PROMPT.format(
                title=doc.title,
                source=doc.source,
                preview=preview,
            )

            response = await self.anthropic_client.messages.create(
                model=self.classifier_model,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}],
            )

            category = response.content[0].text.strip().lower()
            if category in KNOWLEDGE_TYPES:
                return category

            # Fuzzy match
            for t in KNOWLEDGE_TYPES:
                if t in category:
                    return t

            return "topics"
        except Exception as e:
            logger.warning(f"Classification failed for '{doc.title}': {e}")
            return self._fallback_classify(doc)

    def _fallback_classify(self, doc: Document) -> str:
        """Rule-based fallback classification when the LLM is unavailable."""
        title_lower = doc.title.lower()
        content_lower = doc.content[:200].lower() if doc.content else ""
        combined = title_lower + " " + content_lower

        if doc.source == "slack":
            return "topics"  # discussions → topics

        rules = [
            ("decisions", ["adr", "decision", "rfc", "meeting", "minutes", "review", "retro"]),
            ("projects", ["feature", "prd", "user story", "epic", "requirement", "spec", "project"]),
            ("processes", ["runbook", "sop", "deployment", "deploy", "pipeline", "ci/cd",
                           "monitoring", "test", "qa", "quality", "bug", "regression", "coverage"]),
            ("products", ["strategy", "okr", "roadmap", "planning", "quarterly", "vision",
                          "goal", "product", "north star"]),
            ("people", ["expertise", "profile", "team member", "skills"]),
            ("topics", ["architecture", "system design", "api", "infrastructure", "diagram",
                        "schema", "incident", "postmortem", "outage", "alert",
                        "guide", "wiki", "documentation", "onboarding", "how to", "tutorial",
                        "conversation", "thread", "discussion"]),
        ]

        for knowledge_type, keywords in rules:
            if any(kw in combined for kw in keywords):
                return knowledge_type

        return "topics"

    # ── Source path helpers ─────────────────────────────────

    def _get_source_path(self, doc: Document) -> Path:
        """Compute the raw source path for a document.

        Layout:
        - confluence, slack, meetings, emails → sources/{source}/YYYY-MM/{file}.md
        - jira → sources/jira/{project}/{file}.md
        - notion, sharepoint → sources/docs/{source}/{file}.md
        - user_contribution → sources/docs/user_contribution/{file}.md
        """
        sources_dir = self.repo_path / "sources"
        date_partition = doc.updated_at.strftime("%Y-%m")
        filename = sanitize_filename(doc.title) + ".md"

        if doc.source in ("confluence", "slack", "meetings", "emails"):
            partition_dir = sources_dir / doc.source / date_partition
        elif doc.source == "jira":
            # Extract project key from metadata or title
            project = doc.metadata.get("project_key", "general") if doc.metadata else "general"
            partition_dir = sources_dir / "jira" / sanitize_filename(project)
        elif doc.source in ("notion", "sharepoint"):
            partition_dir = sources_dir / "docs" / doc.source
        elif doc.source == "user_contribution":
            partition_dir = sources_dir / "docs" / "user_contribution"
        else:
            partition_dir = sources_dir / "docs" / sanitize_filename(doc.source)

        partition_dir.mkdir(parents=True, exist_ok=True)
        return partition_dir / filename

    def _get_knowledge_path(self, doc: Document, knowledge_type: str) -> Path:
        """Compute the curated knowledge path for a document.

        Layout: knowledge/{type}/{YYYY}-{slug}.md
        For topics with subtopics: knowledge/topics/{subtopic}/{YYYY}-{slug}.md
        """
        knowledge_dir = self.repo_path / "knowledge"
        date_prefix = doc.updated_at.strftime("%Y")
        slug = sanitize_filename(doc.title)
        filename = f"{date_prefix}-{slug}.md"

        if knowledge_type == "topics":
            # Determine subtopic from the fallback classify or default to general
            subtopic = self._determine_subtopic(doc)
            type_dir = knowledge_dir / "topics" / subtopic
        else:
            type_dir = knowledge_dir / knowledge_type

        type_dir.mkdir(parents=True, exist_ok=True)
        return type_dir / filename

    def _determine_subtopic(self, doc: Document) -> str:
        """Determine the subtopic for a topics/ document."""
        title_lower = doc.title.lower()
        content_lower = doc.content[:200].lower() if doc.content else ""
        combined = title_lower + " " + content_lower

        subtopic_rules = [
            ("architecture", ["architecture", "system design", "api", "infrastructure", "diagram", "schema"]),
            ("incidents", ["incident", "postmortem", "outage", "alert", "downtime", "sev1", "sev2"]),
            ("discussions", ["discussion", "conversation", "thread", "q&a"]),
            ("references", ["guide", "wiki", "documentation", "onboarding", "how to", "tutorial", "reference"]),
            ("security", ["security", "vulnerability", "auth", "encryption"]),
            ("infrastructure", ["infra", "cloud", "aws", "gcp", "azure", "kubernetes", "docker"]),
        ]

        # Check source — slack is always discussions
        if doc.source == "slack":
            return "discussions"

        for subtopic, keywords in subtopic_rules:
            if any(kw in combined for kw in keywords):
                return subtopic

        return "general"

    # ── Write document ─────────────────────────────────────

    async def classify_and_write(self, doc: Document) -> str:
        """Classify a document and write it to both sources/ and knowledge/."""
        knowledge_type = await self.classify_document(doc)
        self.write_document(doc, knowledge_type)
        return knowledge_type

    def write_document(self, doc: Document, knowledge_type: str):
        """Write a document to both the raw source and curated knowledge layers."""
        # 1. Write to sources/ (raw, append-only)
        source_path = self._get_source_path(doc)
        self._write_source_file(doc, source_path)

        # 2. Write to knowledge/ (curated, with enriched frontmatter)
        knowledge_path = self._get_knowledge_path(doc, knowledge_type)
        source_ref = str(source_path.relative_to(self.repo_path)).replace("\\", "/")
        self._write_knowledge_file(doc, knowledge_type, knowledge_path, source_ref)

        # 3. Update centralized _index/
        self._update_index(doc, knowledge_type, knowledge_path, source_path)

    def _write_source_file(self, doc: Document, filepath: Path):
        """Write a raw source file with minimal frontmatter."""
        fm_lines = ["---"]
        fm_lines.append(f"id: {doc.id}")
        fm_lines.append(f"source: {doc.source}")
        fm_lines.append(f"title: {doc.title}")
        if doc.url:
            fm_lines.append(f"url: {doc.url}")
        fm_lines.append(f"ingested_at: {datetime.now(timezone.utc).isoformat()}")
        fm_lines.append(f"updated_at: {doc.updated_at.isoformat()}")
        if doc.metadata:
            fm_lines.append("metadata:")
            for k, v in doc.metadata.items():
                fm_lines.append(f"  {k}: {json.dumps(v) if isinstance(v, (list, dict)) else v}")
        fm_lines.append("---")
        fm_lines.append("")

        full_content = "\n".join(fm_lines) + f"# {doc.title}\n\n{doc.content}\n"
        filepath.write_text(full_content, encoding="utf-8")

    def _write_knowledge_file(
        self, doc: Document, knowledge_type: str, filepath: Path, source_ref: str
    ):
        """Write a curated knowledge file with enriched frontmatter."""
        now = datetime.now(timezone.utc)
        date_str = doc.updated_at.strftime("%Y-%m-%d")

        # Build the enriched YAML frontmatter
        fm_lines = ["---"]
        fm_lines.append(f"id: {doc.id}")
        fm_lines.append(f"type: {knowledge_type}")
        fm_lines.append(f"title: {doc.title}")
        fm_lines.append(f"date: {date_str}")
        fm_lines.append("status: active")
        fm_lines.append("supersedes: null")
        fm_lines.append("superseded_by: null")

        # Extract tags from content
        tags = self._extract_tags(doc)
        fm_lines.append(f"tags: {json.dumps(tags)}")

        # Stakeholders / owner
        owner = self._extract_owner(doc)
        fm_lines.append(f"owner: {owner}")

        # Source reference
        fm_lines.append("sources:")
        fm_lines.append(f"  - type: {doc.source}")
        fm_lines.append(f"    ref: {source_ref}")
        if doc.url:
            fm_lines.append(f"    url: {doc.url}")

        fm_lines.append("related: []")
        fm_lines.append(f"freshness_check: null")
        fm_lines.append("confidence: medium")
        fm_lines.append("---")
        fm_lines.append("")

        full_content = "\n".join(fm_lines) + f"# {doc.title}\n\n{doc.content}\n"
        filepath.write_text(full_content, encoding="utf-8")

    def _extract_tags(self, doc: Document) -> list[str]:
        """Extract basic tags from document title and metadata."""
        tags = []
        if doc.source:
            tags.append(doc.source)
        # Extract simple tags from title words
        title_words = re.findall(r'\b[a-zA-Z]{3,}\b', doc.title.lower())
        stop_words = {"the", "and", "for", "with", "from", "this", "that", "are", "was", "has", "have",
                      "will", "can", "not", "but", "all", "any", "each", "how", "its", "may", "use"}
        meaningful = [w for w in title_words if w not in stop_words][:5]
        tags.extend(meaningful)
        return list(dict.fromkeys(tags))  # dedupe preserving order

    def _extract_owner(self, doc: Document) -> str:
        """Extract owner from document metadata or content."""
        if doc.metadata:
            for key in ("author", "owner", "creator", "assigned_to", "assignee"):
                if key in doc.metadata and doc.metadata[key]:
                    return str(doc.metadata[key])
        return ""

    # ── Index management ───────────────────────────────────

    def _update_index(
        self, doc: Document, knowledge_type: str, knowledge_path: Path, source_path: Path
    ):
        """Update the centralized _index/ layer."""
        index_dir = self.repo_path / "_index"
        knowledge_rel = str(knowledge_path.relative_to(self.repo_path)).replace("\\", "/")
        source_rel = str(source_path.relative_to(self.repo_path)).replace("\\", "/")

        # 1. Update graph.json
        self._update_graph(index_dir, doc, knowledge_rel)

        # 2. Update tags.json
        self._update_tags(index_dir, doc, knowledge_rel)

        # 3. Update freshness.json
        self._update_freshness(index_dir, doc, knowledge_rel)

        # 4. Update people.json
        self._update_people(index_dir, doc, knowledge_rel)

    def _update_graph(self, index_dir: Path, doc: Document, knowledge_path: str):
        """Add/update a node in the knowledge graph."""
        graph_path = index_dir / "graph.json"
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            graph = {"nodes": [], "edges": [], "metadata": {"last_rebuilt": None}}

        # Update or add node
        nodes = graph.get("nodes", [])
        existing = next((n for n in nodes if n["id"] == doc.id), None)
        node_data = {
            "id": doc.id,
            "title": doc.title,
            "path": knowledge_path,
            "source": doc.source,
            "updated_at": doc.updated_at.isoformat(),
        }

        if existing:
            existing.update(node_data)
        else:
            nodes.append(node_data)

        graph["nodes"] = nodes
        graph["metadata"]["last_rebuilt"] = datetime.now(timezone.utc).isoformat()
        graph_path.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")

    def _update_tags(self, index_dir: Path, doc: Document, knowledge_path: str):
        """Update the global tag taxonomy."""
        tags_path = index_dir / "tags.json"
        try:
            tags_data = json.loads(tags_path.read_text(encoding="utf-8"))
        except Exception:
            tags_data = {"taxonomy": {}, "metadata": {"last_rebuilt": None}}

        taxonomy = tags_data.get("taxonomy", {})
        tags = self._extract_tags(doc)

        for tag in tags:
            if tag not in taxonomy:
                taxonomy[tag] = []
            if knowledge_path not in taxonomy[tag]:
                taxonomy[tag].append(knowledge_path)

        tags_data["taxonomy"] = taxonomy
        tags_data["metadata"]["last_rebuilt"] = datetime.now(timezone.utc).isoformat()
        tags_path.write_text(json.dumps(tags_data, indent=2, default=str), encoding="utf-8")

    def _update_freshness(self, index_dir: Path, doc: Document, knowledge_path: str):
        """Update the freshness/staleness registry."""
        freshness_path = index_dir / "freshness.json"
        try:
            freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
        except Exception:
            freshness = {"registry": {}, "metadata": {"last_rebuilt": None}}

        registry = freshness.get("registry", {})
        registry[doc.id] = {
            "path": knowledge_path,
            "last_verified": datetime.now(timezone.utc).isoformat(),
            "updated_at": doc.updated_at.isoformat(),
            "owner": self._extract_owner(doc),
        }

        freshness["registry"] = registry
        freshness["metadata"]["last_rebuilt"] = datetime.now(timezone.utc).isoformat()
        freshness_path.write_text(json.dumps(freshness, indent=2, default=str), encoding="utf-8")

    def _update_people(self, index_dir: Path, doc: Document, knowledge_path: str):
        """Update the expertise/people map."""
        people_path = index_dir / "people.json"
        try:
            people = json.loads(people_path.read_text(encoding="utf-8"))
        except Exception:
            people = {"experts": {}, "metadata": {"last_rebuilt": None}}

        owner = self._extract_owner(doc)
        if owner:
            experts = people.get("experts", {})
            if owner not in experts:
                experts[owner] = {"topics": [], "files": []}
            if knowledge_path not in experts[owner]["files"]:
                experts[owner]["files"].append(knowledge_path)
            # Add tags as topics
            for tag in self._extract_tags(doc):
                if tag not in experts[owner]["topics"]:
                    experts[owner]["topics"].append(tag)

            people["experts"] = experts
            people["metadata"]["last_rebuilt"] = datetime.now(timezone.utc).isoformat()
            people_path.write_text(json.dumps(people, indent=2, default=str), encoding="utf-8")

    # ── Pending changes management ─────────────────────────

    def stage_pending(self):
        """Detect all unstaged changes and write a pending changeset summary."""
        if not self._repo:
            return

        # Get all changes
        changed = self._repo.index.diff(None)     # Working tree vs index
        untracked = self._repo.untracked_files

        added = list(untracked)
        modified = [d.a_path for d in changed if d.change_type == "M"]
        deleted = [d.a_path for d in changed if d.change_type == "D"]

        if not added and not modified and not deleted:
            logger.info("No changes to stage")
            return

        # Build per-layer and per-source breakdown
        layer_counts: dict[str, dict] = {}
        source_counts: dict[str, dict] = {}

        for filepath in added + modified:
            parts = Path(filepath).parts
            if len(parts) >= 1:
                # Top-level layer: sources, knowledge, _index, _schema, teams
                layer = parts[0]
                source = parts[1] if len(parts) >= 2 else "unknown"
            else:
                layer = "unknown"
                source = "unknown"

            layer_counts.setdefault(layer, {"added": 0, "modified": 0, "deleted": 0})
            source_counts.setdefault(source, {"added": 0, "modified": 0, "deleted": 0})

            change_type = "added" if filepath in added else "modified"
            layer_counts[layer][change_type] += 1
            source_counts[source][change_type] += 1

        for filepath in deleted:
            parts = Path(filepath).parts
            layer = parts[0] if parts else "unknown"
            source = parts[1] if len(parts) >= 2 else "unknown"
            layer_counts.setdefault(layer, {"added": 0, "modified": 0, "deleted": 0})
            source_counts.setdefault(source, {"added": 0, "modified": 0, "deleted": 0})
            layer_counts[layer]["deleted"] += 1
            source_counts[source]["deleted"] += 1

        changeset = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_added": len(added),
                "total_modified": len(modified),
                "total_deleted": len(deleted),
                "total_changes": len(added) + len(modified) + len(deleted),
            },
            "by_layer": layer_counts,
            "by_source": source_counts,
            "files": {
                "added": added[:500],       # Cap for very large syncs
                "modified": modified[:500],
                "deleted": deleted[:500],
            },
        }

        pending_path = self.state_dir / "pending_changes.json"
        pending_path.write_text(json.dumps(changeset, indent=2), encoding="utf-8")
        logger.info(
            f"Pending changeset: {len(added)} added, {len(modified)} modified, "
            f"{len(deleted)} deleted"
        )

    def get_pending_changes(self) -> dict | None:
        """Get the current pending changeset."""
        pending_path = self.state_dir / "pending_changes.json"
        if pending_path.exists():
            return json.loads(pending_path.read_text(encoding="utf-8"))
        return None

    def get_file_diff(self, file_path: str) -> str:
        """Get the git diff for a specific file."""
        if not self._repo:
            return ""
        try:
            diff = self._repo.git.diff("--", file_path)
            if diff:
                return diff
            # If diff is empty, the file may be untracked/new — show full content
            full_path = self.repo_path / file_path
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8")
                lines = content.splitlines()
                return (
                    f"--- /dev/null\n"
                    f"+++ b/{file_path}\n"
                    f"@@ -0,0 +1,{len(lines)} @@\n"
                    + "\n".join(f"+{line}" for line in lines)
                )
            return ""
        except Exception:
            # Fallback: try reading the file directly
            try:
                full_path = self.repo_path / file_path
                if full_path.exists() and full_path.is_file():
                    content = full_path.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    return (
                        f"--- /dev/null\n"
                        f"+++ b/{file_path}\n"
                        f"@@ -0,0 +1,{len(lines)} @@\n"
                        + "\n".join(f"+{line}" for line in lines)
                    )
            except Exception:
                pass
            return ""

    def approve_commit(self, message: str | None = None) -> dict:
        """Human-approved: commit all pending changes and push to remote.

        Push strategy:
        1. Commits changes on the current (main) branch
        2. Attempts to push directly to main
        3. If the push fails (e.g. conflicts or branch protection),
           creates a timestamped branch and pushes there instead
        """
        if not self._repo:
            return {"error": "Repository not initialized"}

        pending = self.get_pending_changes()
        if not pending:
            return {"error": "No pending changes to commit"}

        summary = pending.get("summary", {})

        # Generate commit message if not provided
        if not message:
            message = (
                f"sync: {summary.get('total_added', 0)} added, "
                f"{summary.get('total_modified', 0)} modified, "
                f"{summary.get('total_deleted', 0)} deleted"
            )

        try:
            current_branch = self._repo.active_branch.name

            # Stage all changes and commit on the current branch
            self._repo.git.add(A=True)
            self._repo.index.commit(message)
            logger.info(f"Committed on {current_branch}: {message}")

            # Push to remote if configured
            push_result = None
            push_branch = current_branch
            if self.remote_url:
                try:
                    # Try pushing directly to the current (main) branch
                    self._repo.git.push("origin", current_branch)
                    push_result = f"success (branch: {current_branch})"
                    logger.info(f"Pushed to '{current_branch}' successfully")
                except Exception as main_err:
                    logger.warning(
                        f"Direct push to '{current_branch}' failed: {main_err}. "
                        f"Falling back to a new branch."
                    )
                    # Fallback: create a timestamped branch from the commit
                    # and push that instead
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                    fallback_branch = f"grasp/sync-{timestamp}"

                    self._repo.git.branch(fallback_branch)
                    logger.info(f"Created fallback branch: {fallback_branch}")

                    try:
                        self._repo.git.push("--set-upstream", "origin", fallback_branch)
                        push_result = f"success (fallback branch: {fallback_branch})"
                        push_branch = fallback_branch
                        logger.info(f"Pushed fallback branch '{fallback_branch}' to remote")
                    except Exception as fallback_err:
                        push_result = f"push_failed: {fallback_err}"
                        logger.error(f"Fallback push also failed: {fallback_err}")

            # Clear pending changes
            pending_path = self.state_dir / "pending_changes.json"
            if pending_path.exists():
                pending_path.unlink()

            return {
                "status": "committed",
                "message": message,
                "push": push_result,
                "branch": push_branch,
                "changes": summary,
            }
        except Exception as e:
            logger.error(f"Commit failed: {e}")
            return {"error": str(e)}

    def reject_changes(self) -> dict:
        """Revert all uncommitted changes."""
        if not self._repo:
            return {"error": "Repository not initialized"}

        try:
            # Revert working tree changes
            self._repo.git.checkout("--", ".")

            # Remove untracked files, but preserve .grasp_state/
            self._repo.git.clean("-fd", "--exclude=.grasp_state/")

            # Clear pending changes file
            pending_path = self.state_dir / "pending_changes.json"
            if pending_path.exists():
                pending_path.unlink()

            logger.info("All pending changes rejected and reverted")
            return {"status": "rejected"}
        except Exception as e:
            logger.error(f"Reject failed: {e}")
            return {"error": str(e)}

    # ── Read operations ────────────────────────────────────

    def get_file_content(self, file_path: str) -> str:
        """Read the content of a file from the repository."""
        full_path = self.repo_path / file_path
        if full_path.exists() and full_path.is_file():
            return full_path.read_text(encoding="utf-8")
        return ""

    def search_files(self, query: str) -> list[str]:
        """Search for files by name/path in the repository."""
        query_lower = query.lower()
        results = []
        for path in self.repo_path.rglob("*.md"):
            rel = str(path.relative_to(self.repo_path))
            if query_lower in rel.lower() or query_lower in path.stem.lower():
                results.append(rel)
        return results[:50]

    def get_source_stats(self) -> dict:
        """Get document counts per source and per knowledge type."""
        stats = {"by_type": {}, "by_source": {}, "total": 0}

        # Count from knowledge/ layer
        knowledge_dir = self.repo_path / "knowledge"
        if knowledge_dir.exists():
            for knowledge_type in KNOWLEDGE_TYPES:
                type_dir = knowledge_dir / knowledge_type
                if not type_dir.exists():
                    continue

                # Count .md files (excluding README.md)
                md_files = [
                    f for f in type_dir.rglob("*.md")
                    if f.name != "README.md"
                ]
                type_count = len(md_files)

                if type_count > 0:
                    stats["by_type"][knowledge_type] = type_count
                    stats["total"] += type_count

        # Count from sources/ layer by source platform
        sources_dir = self.repo_path / "sources"
        if sources_dir.exists():
            for source_dir in sources_dir.iterdir():
                if source_dir.is_dir():
                    if source_dir.name == "docs":
                        # Count per sub-source (notion, sharepoint, etc.)
                        for sub_dir in source_dir.iterdir():
                            if sub_dir.is_dir():
                                count = len(list(sub_dir.rglob("*.md")))
                                if count > 0:
                                    stats["by_source"][sub_dir.name] = (
                                        stats["by_source"].get(sub_dir.name, 0) + count
                                    )
                    else:
                        count = len(list(source_dir.rglob("*.md")))
                        if count > 0:
                            stats["by_source"][source_dir.name] = (
                                stats["by_source"].get(source_dir.name, 0) + count
                            )

        return stats
