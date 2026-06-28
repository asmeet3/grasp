"""Grasp — Agentic Institutional Brain

Entry point: initializes all components and launches the server.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv()

from src.config import load_settings
from src.connectors.confluence import ConfluenceConnector
from src.connectors.jira import JiraConnector
from src.connectors.sharepoint import SharePointConnector
from src.connectors.slack import SlackConnector
from src.connectors.notion import NotionConnector
from src.connectors.base import BaseConnector, Document
from src.sync.checkpoints import CheckpointManager
from src.sync.orchestrator import SyncOrchestrator
from src.sync.scheduler import SyncScheduler
from src.repo.manager import RepoManager
from src.index.vector_store import VectorStore
from src.agent.sub_agents import SubAgent, SubAgentDispatcher
from src.agent.tools import ToolExecutor
from src.agent.engine import QueryEngine
from src.api.server import create_app
from src.contributions import ContributionManager
from src.auth import UserManager

# ── Logging ────────────────────────────────────────────────

import io as _io

# Force UTF-8 on stdout so the │ character in log lines doesn't crash on
# Windows consoles that default to CP1252.
_utf8_stdout = _io.TextIOWrapper(
    sys.stdout.buffer,
    encoding="utf-8",
    errors="replace",
    line_buffering=True,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-30s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(_utf8_stdout)],
)
logger = logging.getLogger("grasp")



def build_connectors(settings) -> dict[str, BaseConnector]:
    """Initialize all configured platform connectors."""
    connectors: dict[str, BaseConnector] = {}

    if settings.is_connector_configured("confluence"):
        connectors["confluence"] = ConfluenceConnector(
            base_url=settings.confluence_url,
            email=settings.confluence_email,
            api_token=settings.confluence_api_token,
            batch_size=settings.sync_batch_size,
        )
        logger.info("✓ Confluence connector initialized")

    if settings.is_connector_configured("jira"):
        connectors["jira"] = JiraConnector(
            base_url=settings.jira_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            batch_size=settings.sync_batch_size,
        )
        logger.info("✓ Jira connector initialized")

    if settings.is_connector_configured("sharepoint"):
        connectors["sharepoint"] = SharePointConnector(
            tenant_id=settings.sharepoint_tenant_id,
            client_id=settings.sharepoint_client_id,
            client_secret=settings.sharepoint_client_secret,
            site_id=settings.sharepoint_site_id,
            batch_size=settings.sync_batch_size,
        )
        logger.info("✓ SharePoint connector initialized")

    if settings.is_connector_configured("slack"):
        connectors["slack"] = SlackConnector(
            bot_token=settings.slack_bot_token,
            batch_size=settings.sync_batch_size,
        )
        logger.info("✓ Slack connector initialized")

    if settings.is_connector_configured("notion"):
        connectors["notion"] = NotionConnector(
            api_key=settings.notion_api_key,
            batch_size=settings.sync_batch_size,
        )
        logger.info("✓ Notion connector initialized")

    if not connectors:
        logger.warning("⚠ No connectors configured! Add credentials to .env")

    return connectors


def build_sub_agent_dispatcher(
    connectors: dict[str, BaseConnector],
    vector_store: VectorStore,
) -> SubAgentDispatcher:
    """Build the sub-agent dispatcher for parallel query fan-out."""
    dispatcher = SubAgentDispatcher()

    # Repo search sub-agent (wraps vector store)
    async def repo_search(query: str) -> list[Document]:
        results = vector_store.search(query, n_results=10)
        return [
            Document(
                id=r.doc_id,
                source=r.source,
                title=r.title,
                content=r.content,
                url=r.url,
            )
            for r in results
        ]

    dispatcher.register(SubAgent(
        name="repo_search",
        source="knowledge_repo",
        search_fn=repo_search,
        timeout=5.0,
    ))

    # Live platform sub-agents
    for name, connector in connectors.items():
        dispatcher.register(SubAgent(
            name=f"{name}_live",
            source=name,
            search_fn=connector.live_search,
            timeout=10.0,
        ))

    return dispatcher


def main():
    """Main entry point — initialize and launch Grasp."""
    logger.info("=" * 60)
    logger.info("  GRASP — Agentic Institutional Brain")
    logger.info("=" * 60)

    # 1. Load configuration
    try:
        settings = load_settings()
        logger.info(f"✓ Configuration loaded ({len(settings.get_configured_connectors())} connectors configured)")
    except Exception as e:
        logger.error(f"✗ Configuration error: {e}")
        logger.error("  Copy .env.example to .env and fill in your credentials")
        sys.exit(1)

    # 2. Initialize components
    connectors = build_connectors(settings)

    repo_manager = RepoManager(
        repo_path=settings.repo_path,
        anthropic_api_key=settings.anthropic_api_key,
        classifier_model=settings.classifier_model,
        remote_url=settings.github_remote_url,
        github_pat=settings.github_pat,
    )
    logger.info(f"✓ Repository manager initialized at {settings.repo_path}")

    vector_store = VectorStore(persist_dir=settings.chroma_path)
    logger.info(f"✓ Vector store initialized ({vector_store.document_count} chunks indexed)")

    checkpoint_manager = CheckpointManager(settings.checkpoints_path)
    logger.info("✓ Checkpoint manager initialized")

    # 3. Sync orchestrator
    state_dir = settings.repo_path / ".grasp_state"
    orchestrator = SyncOrchestrator(
        connectors=connectors,
        repo_manager=repo_manager,
        vector_store=vector_store,
        checkpoints=checkpoint_manager,
        state_dir=state_dir,
    )
    logger.info("✓ Sync orchestrator initialized")

    # 4. Scheduler
    scheduler = SyncScheduler(
        orchestrator=orchestrator,
        hours=settings.sync_cron_hours,
        minute=settings.sync_cron_minute,
    )

    # 5. Query engine
    dispatcher = build_sub_agent_dispatcher(connectors, vector_store)
    tool_executor = ToolExecutor(
        dispatcher=dispatcher,
        vector_store=vector_store,
        repo_manager=repo_manager,
        connectors=connectors,
    )
    query_engine = QueryEngine(
        anthropic_api_key=settings.anthropic_api_key,
        model=settings.agent_model,
        tool_executor=tool_executor,
    )
    logger.info(f"✓ Query engine initialized (model: {settings.agent_model})")

    # 5b. Contribution manager
    contribution_manager = ContributionManager(
        state_dir=state_dir,
        repo_manager=repo_manager,
    )
    logger.info("✓ Contribution manager initialized")

    # 5c. User manager
    user_manager = UserManager(
        state_dir=state_dir,
        session_secret=settings.effective_session_secret,
        google_client_id=settings.google_client_id,
    )
    logger.info("✓ User manager initialized")

    # 6. FastAPI app
    app = create_app(
        query_engine=query_engine,
        sync_orchestrator=orchestrator,
        sync_scheduler=scheduler,
        repo_manager=repo_manager,
        vector_store=vector_store,
        connectors=connectors,
        admin_key=settings.admin_key,
        contribution_manager=contribution_manager,
        user_manager=user_manager,
        google_client_id=settings.google_client_id,
    )

    # 7. Startup event — start scheduler
    @app.on_event("startup")
    async def on_startup():
        loop = asyncio.get_event_loop()
        scheduler.start(loop=loop)
        logger.info("✓ Scheduler started")

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler.stop()
        # Close all connector HTTP clients
        for connector in connectors.values():
            if hasattr(connector, 'close'):
                await connector.close()
        logger.info("Shutdown complete")

    # 8. Launch
    logger.info(f"Starting server on {settings.host}:{settings.port}")
    logger.info(f"Dashboard: http://localhost:{settings.port}")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
