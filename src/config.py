"""Centralized configuration using Pydantic BaseSettings.

All settings are loaded from environment variables or a .env file.
Validation runs at startup to catch misconfigurations early.
"""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application settings — loaded from .env and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Anthropic ──────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    agent_model: str = Field("claude-sonnet-4-6", description="Model for agentic reasoning")
    classifier_model: str = Field("claude-haiku-4-5-20251001", description="Model for content classification")
    query_shortener_model: str = Field("claude-haiku-4-5-20251001", description="Model for query shortening (fast/cheap)")
    query_shortener_system_prompt: str = Field("""You are a query-shortening assistant for an enterprise search and RAG system.

Your task is to convert a user's natural-language question into one or more short, keyword-focused search queries.

Rules:
1. Remove conversational filler, articles, pronouns, question words, and unnecessary grammar.
2. Keep the core subject, entity names, project names, API names, product names, status terms, and important qualifiers.
3. Preserve exact technical identifiers exactly as written, including underscores, hyphens, casing where possible, and special terms such as API names.
4. Do not add information, assumptions, synonyms, or explanations not present in the user query.
5. Prefer concise noun phrases or keyword groups, typically 2 to 6 words.
6. Do not use full sentences, punctuation, or question marks in shortened queries.
7. Return one query when all parts of the question concern the same subject or closely related information.
8. Return multiple queries only when the question covers distinct or mutually exclusive entities, systems, applications, teams, projects, or topics that should be searched independently.
9. For comparisons between separate entities, return one shortened query for each entity. Do not include words such as `difference`, `versus`, or `vs`.
10. If multiple independent questions share relevant context, retain that context in each query where necessary for accurate retrieval.
11. Maintain the original language used by the user.
12. Return only a valid Python list of double-quoted strings. Do not add markdown, explanations, labels, or code fences.
""", description="System prompt for the query shortener LLM")

    # ── OpenAI (Embeddings) ────────────────────────────────
    openai_api_key: str = Field("", description="OpenAI API key for embeddings")
    embedding_model: str = Field("text-embedding-3-large", description="OpenAI embedding model name")

    # ── GitHub Repository ──────────────────────────────────
    github_repo_path: str = Field("./knowledge_repo", description="Local path for the knowledge repo")
    github_remote_url: str = Field("", description="Remote Git URL for push")
    github_pat: str = Field("", description="GitHub Personal Access Token")

    # ── Confluence ─────────────────────────────────────────
    confluence_url: str = Field("", description="Confluence base URL")
    confluence_email: str = Field("", description="Confluence account email")
    confluence_api_token: str = Field("", description="Confluence API token")

    # ── Jira ───────────────────────────────────────────────
    jira_url: str = Field("", description="Jira base URL")
    jira_email: str = Field("", description="Jira account email")
    jira_api_token: str = Field("", description="Jira API token")

    # ── SharePoint (Microsoft Graph) ──────────────────────
    sharepoint_tenant_id: str = Field("", description="Azure tenant ID")
    sharepoint_client_id: str = Field("", description="Azure app client ID")
    sharepoint_client_secret: str = Field("", description="Azure app client secret")
    sharepoint_site_id: str = Field("", description="SharePoint site ID")

    # ── Slack ──────────────────────────────────────────────
    slack_bot_token: str = Field("", description="Slack bot token")

    # ── Notion ─────────────────────────────────────────────
    notion_api_key: str = Field("", description="Notion integration key")

    # ── Sync Schedule ──────────────────────────────────────
    # Default times: 08:00, 11:00, 14:00, 17:00, 20:00 IST → 02:30, 05:30, 08:30, 11:30, 14:30 UTC
    sync_cron_hours: list[int] = Field(
        default=[2, 5, 8, 11, 14],
        description="Hours (UTC) to run sync during working hours",
    )
    sync_cron_minute: int = Field(30, description="Minute for sync runs")
    sync_batch_size: int = Field(100, description="Documents per batch during sync")

    # ── Server ─────────────────────────────────────────────
    host: str = Field("0.0.0.0", description="Server bind host")
    port: int = Field(8000, description="Server bind port")
    admin_key: str = Field(..., description="Secret key for admin endpoints (sync, approve, reject)")
    google_client_id: str = Field("", description="Google OAuth 2.0 Client ID for sign-in")
    session_secret: str = Field("", description="Secret for signing session tokens (falls back to admin_key)")

    # ── Database ───────────────────────────────────────────
    database_url: str = Field(
        "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp",
        description="PostgreSQL connection URL",
    )

    @property
    def effective_session_secret(self) -> str:
        """Return the session signing secret, falling back to admin_key."""
        return self.session_secret or self.admin_key

    # ── Derived paths ──────────────────────────────────────
    @property
    def repo_path(self) -> Path:
        return Path(self.github_repo_path).resolve()

    @property
    def chroma_path(self) -> Path:
        return Path("./chroma_data").resolve()

    @property
    def checkpoints_path(self) -> Path:
        return Path("./checkpoints").resolve()

    def is_connector_configured(self, name: str) -> bool:
        """Check if a given connector has its required credentials set."""
        checks = {
            "confluence": bool(self.confluence_url and self.confluence_api_token),
            "jira": bool(self.jira_url and self.jira_api_token),
            "sharepoint": bool(self.sharepoint_tenant_id and self.sharepoint_client_id and self.sharepoint_client_secret),
            "slack": bool(self.slack_bot_token),
            "notion": bool(self.notion_api_key),
        }
        return checks.get(name, False)

    def get_configured_connectors(self) -> list[str]:
        """Return list of connector names that have valid credentials."""
        return [name for name in ["confluence", "jira", "sharepoint", "slack", "notion"]
                if self.is_connector_configured(name)]


def load_settings() -> Settings:
    """Load and validate settings. Raises ValidationError on bad config."""
    return Settings()
