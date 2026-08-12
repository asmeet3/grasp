"""Centralized configuration using Pydantic BaseSettings.

All settings are loaded from environment variables or a .env file.
Validation runs at startup to catch misconfigurations early.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — loaded from .env and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider — "anthropic" uses the official Anthropic SDK; "deepseek"
    # uses the OpenAI-compatible DeepSeek shim. Each provider has its own key
    # field; call sites receive the effective key via llm_api_key.
    llm_provider: str = Field("anthropic", description="LLM provider: anthropic or deepseek")
    anthropic_api_key: str = Field(
        "", description="Anthropic API key (required when LLM_PROVIDER=anthropic)"
    )
    deepseek_api_key: str = Field(
        "", description="DeepSeek API key (required when LLM_PROVIDER=deepseek)"
    )
    agent_model: str = Field("claude-sonnet-4-6", description="Model for agentic reasoning")
    classifier_model: str = Field(
        "claude-haiku-4-5-20251001", description="Model for content classification"
    )
    query_shortener_model: str = Field(
        "claude-haiku-4-5-20251001",
        description="Model for query shortening",
    )
    query_shortener_system_prompt: str = Field(
        """Convert the user's question into concise keyword search queries.
Preserve names, technical identifiers, important qualifiers, and the user's language.
Remove conversational filler without adding assumptions or synonyms.
Split only when distinct entities or topics need independent searches; return at most three queries.
Return only a JSON array of strings, with no markdown or explanation.""",
        description="System prompt for the query shortener LLM",
    )

    # OpenAI embeddings
    openai_api_key: str = Field("", description="OpenAI API key for embeddings")
    embedding_model: str = Field(
        "text-embedding-3-large", description="OpenAI embedding model name"
    )

    # Knowledge repository
    github_repo_path: str = Field(
        "./knowledge_repo", description="Local path for the knowledge repo"
    )
    github_remote_url: str = Field("", description="Remote Git URL for push")
    github_pat: str = Field("", description="GitHub Personal Access Token")

    # Confluence
    confluence_url: str = Field("", description="Confluence base URL")
    confluence_email: str = Field("", description="Confluence account email")
    confluence_api_token: str = Field("", description="Confluence API token")

    # Jira
    jira_url: str = Field("", description="Jira base URL")
    jira_email: str = Field("", description="Jira account email")
    jira_api_token: str = Field("", description="Jira API token")

    # SharePoint
    sharepoint_tenant_id: str = Field("", description="Azure tenant ID")
    sharepoint_client_id: str = Field("", description="Azure app client ID")
    sharepoint_client_secret: str = Field("", description="Azure app client secret")
    sharepoint_site_id: str = Field("", description="SharePoint site ID")

    # Slack
    slack_bot_token: str = Field("", description="Slack bot token")

    # Notion
    notion_api_key: str = Field("", description="Notion integration key")

    # Sync schedule
    # Default times: 08:00, 11:00, 14:00, 17:00, 20:00 IST → 02:30, 05:30, 08:30, 11:30, 14:30 UTC
    sync_cron_hours: list[int] = Field(
        default=[2, 5, 8, 11, 14],
        description="Hours (UTC) to run sync during working hours",
    )
    sync_cron_minute: int = Field(30, description="Minute for sync runs")
    sync_batch_size: int = Field(100, description="Documents per batch during sync")

    # Server
    host: str = Field(
        "0.0.0.0",  # nosec B104 - deployed servers must accept external connections.
        description="Server bind host",
    )
    port: int = Field(8000, description="Server bind port")
    admin_key: str = Field(
        ..., description="Secret key for admin endpoints (sync, approve, reject)"
    )
    google_client_id: str = Field("", description="Google OAuth 2.0 Client ID for sign-in")
    session_secret: str = Field(
        "", description="Secret for signing session tokens (falls back to admin_key)"
    )
    access_token_max_age_seconds: int = Field(3600, ge=300, le=86400)
    trusted_origins: list[str] = Field(
        default=["http://localhost:8000", "http://127.0.0.1:8000"],
        description="Exact browser origins allowed by CORS",
    )
    upload_max_bytes: int = Field(10 * 1024 * 1024, ge=1024)
    upload_max_pages: int = Field(200, ge=1)
    upload_max_text_chars: int = Field(2_000_000, ge=1000)
    auth_rate_limit_per_minute: int = Field(20, ge=1)
    query_rate_limit_per_minute: int = Field(60, ge=1)
    upload_rate_limit_per_minute: int = Field(10, ge=1)

    # Independent rollout flags. Write capabilities default off; governed,
    # read-only company-brain agents are live by default.
    auth_required: bool = True
    revisioned_knowledge: bool = True
    context_routing: bool = False
    structured_memory: bool = False
    provider_routing: bool = False
    agents_enabled: bool = True
    self_improvement_enabled: bool = False
    context_token_budget: int = Field(8000, ge=1000)
    max_live_providers: int = Field(2, ge=0, le=10)
    sync_overlap_seconds: int = Field(300, ge=0)
    worker_poll_seconds: float = Field(1.0, ge=0.1, le=60.0)
    worker_concurrency: int = Field(4, ge=1, le=32)
    observability_flush_seconds: int = Field(
        300,
        ge=10,
        le=3600,
        description="How often live metric snapshots are persisted for session history",
    )

    # Database
    database_url: str = Field(
        "postgresql+asyncpg://grasp:grasp@localhost:5432/grasp",
        description="PostgreSQL connection URL",
    )

    @model_validator(mode="after")
    def validate_llm_configuration(self):
        provider = self.llm_provider.strip().lower()
        if provider not in {"anthropic", "deepseek"}:
            raise ValueError(
                f"LLM_PROVIDER must be 'anthropic' or 'deepseek', got {self.llm_provider!r}"
            )
        required_key = "ANTHROPIC_API_KEY" if provider == "anthropic" else "DEEPSEEK_API_KEY"
        configured_key = (
            self.anthropic_api_key if provider == "anthropic" else self.deepseek_api_key
        )
        if not configured_key:
            raise ValueError(f"LLM_PROVIDER={provider} requires {required_key} to be set")
        return self

    @model_validator(mode="after")
    def validate_safe_rollout_order(self):
        if not self.auth_required or not self.revisioned_knowledge:
            if self.agents_enabled or self.self_improvement_enabled:
                raise ValueError(
                    "Agents and self-improvement require authentication and revisioned knowledge"
                )
        return self

    @property
    def llm_api_key(self) -> str:
        """Return the API key for the configured LLM provider."""
        if self.llm_provider.strip().lower() == "deepseek":
            return self.deepseek_api_key
        return self.anthropic_api_key

    @property
    def effective_session_secret(self) -> str:
        """Return the session signing secret, falling back to admin_key."""
        return self.session_secret or self.admin_key

    # Derived paths
    @property
    def repo_path(self) -> Path:
        return Path(self.github_repo_path).resolve()

    @property
    def chroma_path(self) -> Path:
        return Path("./chroma_data").resolve()

    def is_connector_configured(self, name: str) -> bool:
        """Check if a given connector has its required credentials set."""
        checks = {
            "confluence": bool(self.confluence_url and self.confluence_api_token),
            "jira": bool(self.jira_url and self.jira_api_token),
            "sharepoint": bool(
                self.sharepoint_tenant_id
                and self.sharepoint_client_id
                and self.sharepoint_client_secret
            ),
            "slack": bool(self.slack_bot_token),
            "notion": bool(self.notion_api_key),
        }
        return checks.get(name, False)

    def get_configured_connectors(self) -> list[str]:
        """Return list of connector names that have valid credentials."""
        return [
            name
            for name in ["confluence", "jira", "sharepoint", "slack", "notion"]
            if self.is_connector_configured(name)
        ]


def load_settings() -> Settings:
    """Load and validate settings. Raises ValidationError on bad config."""
    return Settings()
