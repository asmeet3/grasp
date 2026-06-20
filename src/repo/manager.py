"""Repository manager — Git-backed knowledge storage with type classification.

Documents are organized by information type (classified via Claude Haiku)
and source platform. Supports human-approved commits with remote push.
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

# Information type taxonomy
INFO_TYPES = [
    "architecture",  # System design, APIs, infrastructure
    "features",      # Feature specs, PRDs, user stories
    "operations",    # Runbooks, SOPs, deployment guides
    "testing",       # Test plans, QA docs, test results
    "decisions",     # ADRs, meeting notes, RFCs
    "strategy",      # Business strategy, OKRs, roadmaps
    "incidents",     # Incident reports, postmortems
    "discussions",   # Conversations, threads, Q&A
    "references",    # General docs, wikis, guides
    "general",       # Uncategorized
]

CLASSIFICATION_PROMPT = """You are a document classifier for a company knowledge base. Classify the following document into exactly ONE of these categories based on its title and content:

Categories:
- architecture: System design, APIs, infrastructure diagrams, technical architecture
- features: Feature specs, PRDs, user stories, epics, feature development
- operations: Runbooks, SOPs, deployment guides, infrastructure operations
- testing: Test plans, QA documentation, test results, bug reports
- decisions: ADRs, meeting notes, RFCs, design reviews, decision records
- strategy: Business strategy, OKRs, roadmaps, planning documents
- incidents: Incident reports, postmortems, outage logs, incident responses
- discussions: Conversations, chat threads, async Q&A, team discussions
- references: General documentation, wikis, guides, onboarding materials
- general: Anything that doesn't clearly fit the above categories

Document Title: {title}
Document Source: {source}
Content Preview (first 500 chars): {preview}

Respond with ONLY the category name, nothing else."""


class RepoManager:
    """Manages the Git-backed knowledge repository."""

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
        """Initialize or open the Git repository."""
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Create directory structure
        for info_type in INFO_TYPES:
            (self.repo_path / info_type).mkdir(exist_ok=True)

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
                "Content is organized by information type and source platform.\n",
                encoding="utf-8",
            )
            self._repo.index.add(["README.md", ".gitignore"])
            self._repo.index.commit("Initial repository setup")
            logger.info(f"Initialized new Git repository at {self.repo_path}")

        # Configure remote if provided
        if self.remote_url:
            self._configure_remote()

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
        """Classify a document into an information type using Claude Haiku."""
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
            if category in INFO_TYPES:
                return category

            # Fuzzy match
            for t in INFO_TYPES:
                if t in category:
                    return t

            return "general"
        except Exception as e:
            logger.warning(f"Classification failed for '{doc.title}': {e}")
            return self._fallback_classify(doc)

    def _fallback_classify(self, doc: Document) -> str:
        """Rule-based fallback classification when the LLM is unavailable."""
        title_lower = doc.title.lower()
        content_lower = doc.content[:200].lower() if doc.content else ""
        combined = title_lower + " " + content_lower

        if doc.source == "slack":
            return "discussions"

        rules = [
            ("architecture", ["architecture", "system design", "api", "infrastructure", "diagram", "schema"]),
            ("features", ["feature", "prd", "user story", "epic", "requirement", "spec"]),
            ("operations", ["runbook", "sop", "deployment", "deploy", "pipeline", "ci/cd", "monitoring"]),
            ("testing", ["test", "qa", "quality", "bug", "regression", "coverage"]),
            ("decisions", ["adr", "decision", "rfc", "meeting", "minutes", "review"]),
            ("strategy", ["strategy", "okr", "roadmap", "planning", "quarterly", "vision", "goal"]),
            ("incidents", ["incident", "postmortem", "outage", "alert", "downtime", "sev1", "sev2"]),
            ("references", ["guide", "wiki", "documentation", "onboarding", "how to", "tutorial"]),
        ]

        for info_type, keywords in rules:
            if any(kw in combined for kw in keywords):
                return info_type

        return "general"

    # ── Write document ─────────────────────────────────────

    async def classify_and_write(self, doc: Document) -> str:
        """Classify a document and write it to the appropriate directory."""
        info_type = await self.classify_document(doc)
        self.write_document(doc, info_type)
        return info_type

    def write_document(self, doc: Document, info_type: str):
        """Write a document to the repo as a Markdown file with YAML frontmatter."""
        # Build path: {info_type}/{source}/{sanitized_title}.md
        source_dir = self.repo_path / info_type / doc.source
        source_dir.mkdir(parents=True, exist_ok=True)

        filename = sanitize_filename(doc.title) + ".md"
        filepath = source_dir / filename

        # Build YAML frontmatter
        frontmatter = {
            "id": doc.id,
            "source": doc.source,
            "title": doc.title,
            "url": doc.url,
            "info_type": info_type,
            "updated_at": doc.updated_at.isoformat(),
        }
        if doc.metadata:
            frontmatter["metadata"] = doc.metadata

        # Format as YAML
        fm_lines = ["---"]
        for key, value in frontmatter.items():
            if isinstance(value, dict):
                fm_lines.append(f"{key}:")
                for k, v in value.items():
                    fm_lines.append(f"  {k}: {json.dumps(v) if isinstance(v, (list, dict)) else v}")
            else:
                fm_lines.append(f"{key}: {json.dumps(value) if isinstance(value, (list, dict)) else value}")
        fm_lines.append("---")
        fm_lines.append("")

        # Write file
        full_content = "\n".join(fm_lines) + f"# {doc.title}\n\n{doc.content}\n"
        filepath.write_text(full_content, encoding="utf-8")

        # Update index
        self._update_index(info_type, doc)

    def _update_index(self, info_type: str, doc: Document):
        """Update the _index.json for the given info_type directory."""
        index_path = self.repo_path / info_type / "_index.json"

        index: dict[str, Any] = {}
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                index = {}

        entries = index.get("entries", {})
        entries[doc.id] = {
            "title": doc.title,
            "source": doc.source,
            "url": doc.url,
            "updated_at": doc.updated_at.isoformat(),
            "path": f"{doc.source}/{sanitize_filename(doc.title)}.md",
        }

        index["entries"] = entries
        index["count"] = len(entries)
        index["last_updated"] = datetime.now(timezone.utc).isoformat()

        index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")

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

        # Build per-type and per-source breakdown
        type_counts: dict[str, dict] = {}
        source_counts: dict[str, dict] = {}

        for filepath in added + modified:
            parts = Path(filepath).parts
            if len(parts) >= 2:
                info_type = parts[0]
                source = parts[1] if len(parts) >= 3 else "unknown"
            else:
                info_type = "unknown"
                source = "unknown"

            type_counts.setdefault(info_type, {"added": 0, "modified": 0, "deleted": 0})
            source_counts.setdefault(source, {"added": 0, "modified": 0, "deleted": 0})

            change_type = "added" if filepath in added else "modified"
            type_counts[info_type][change_type] += 1
            source_counts[source][change_type] += 1

        for filepath in deleted:
            parts = Path(filepath).parts
            info_type = parts[0] if parts else "unknown"
            source = parts[1] if len(parts) >= 3 else "unknown"
            type_counts.setdefault(info_type, {"added": 0, "modified": 0, "deleted": 0})
            source_counts.setdefault(source, {"added": 0, "modified": 0, "deleted": 0})
            type_counts[info_type]["deleted"] += 1
            source_counts[source]["deleted"] += 1

        changeset = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_added": len(added),
                "total_modified": len(modified),
                "total_deleted": len(deleted),
                "total_changes": len(added) + len(modified) + len(deleted),
            },
            "by_type": type_counts,
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
            return self._repo.git.diff("--", file_path)
        except Exception:
            # If the file is untracked, show the full content
            try:
                full_path = self.repo_path / file_path
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8")
                    return f"+++ {file_path} (new file)\n" + "\n".join(
                        f"+{line}" for line in content.splitlines()
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
        """Get document counts per source and per type."""
        stats = {"by_type": {}, "by_source": {}, "total": 0}

        for info_type in INFO_TYPES:
            type_dir = self.repo_path / info_type
            if not type_dir.exists():
                continue

            type_count = 0
            for source_dir in type_dir.iterdir():
                if source_dir.is_dir() and source_dir.name != ".grasp_state":
                    count = len(list(source_dir.glob("*.md")))
                    type_count += count
                    stats["by_source"][source_dir.name] = (
                        stats["by_source"].get(source_dir.name, 0) + count
                    )

            if type_count > 0:
                stats["by_type"][info_type] = type_count
                stats["total"] += type_count

        return stats
