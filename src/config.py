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
