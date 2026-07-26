"""Grasp — Agentic Institutional Brain

Entry point: initializes all components and launches the server.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid

import uvicorn

from src.agent.engine import QueryEngine
from src.agent.query_shortener import QueryShortener
from src.agent.sub_agents import SubAgent, SubAgentDispatcher
from src.agent.tools import ToolExecutor
from src.agents import AgentScheduler, AgentService
from src.api.server import create_app
from src.audit import PostgresAuditStore
from src.auth import UserManager
from src.changesets import ChangeSetService
from src.chat_manager import ChatManager
from src.config import load_settings
from src.connectors.base import BaseConnector, Document
from src.connectors.confluence import ConfluenceConnector
from src.connectors.jira import JiraConnector
from src.connectors.notion import NotionConnector
from src.connectors.sharepoint import SharePointConnector
from src.connectors.slack import SlackConnector
from src.context_router import ContextRouter
from src.contributions import ContributionManager
from src.core.security import AuthContext, PolicyEngine
from src.database import create_engine, init_db
from src.index.vector_store import VectorStore
from src.jobs import PostgresJobQueue
from src.observability import MetricRecorder, SecretRedactionFilter
from src.providers import ConnectorProvider, ProviderRouter
from src.repo.manager import RepoManager
from src.sync.checkpoints import CheckpointManager
from src.sync.orchestrator import SyncOrchestrator
from src.sync.scheduler import SyncScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
for handler in logging.getLogger().handlers:
    handler.addFilter(SecretRedactionFilter())
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
        logger.info("Confluence connector initialized")

    if settings.is_connector_configured("jira"):
        connectors["jira"] = JiraConnector(
            base_url=settings.jira_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            batch_size=settings.sync_batch_size,
        )
        logger.info("Jira connector initialized")

    if settings.is_connector_configured("sharepoint"):
        connectors["sharepoint"] = SharePointConnector(
            tenant_id=settings.sharepoint_tenant_id,
            client_id=settings.sharepoint_client_id,
            client_secret=settings.sharepoint_client_secret,
            site_id=settings.sharepoint_site_id,
            batch_size=settings.sync_batch_size,
        )
        logger.info("SharePoint connector initialized")

    if settings.is_connector_configured("slack"):
        connectors["slack"] = SlackConnector(
            bot_token=settings.slack_bot_token,
            batch_size=settings.sync_batch_size,
        )
        logger.info("Slack connector initialized")

    if settings.is_connector_configured("notion"):
        connectors["notion"] = NotionConnector(
            api_key=settings.notion_api_key,
            batch_size=settings.sync_batch_size,
        )
        logger.info("Notion connector initialized")

    if not connectors:
        logger.warning("No connectors configured; add credentials to .env")

    return connectors


def build_sub_agent_dispatcher(
    connectors: dict[str, BaseConnector],
    vector_store: VectorStore,
    query_shortener: QueryShortener | None = None,
    provider_router: ProviderRouter | None = None,
) -> SubAgentDispatcher:
    """Build the sub-agent dispatcher for parallel query fan-out."""
    dispatcher = SubAgentDispatcher(
        query_shortener=query_shortener, provider_router=provider_router
    )

    async def repo_search(query: str, context: AuthContext) -> list[Document]:
        results = await asyncio.to_thread(
            vector_store.search, query, n_results=10, auth_context=context
        )
        return [
            Document(
                id=r.doc_id,
                source=r.source,
                title=r.title,
                content=r.content,
                url=r.url,
                metadata={
                    "repo_path": r.repo_path,
                    "info_type": r.info_type,
                    "score": round(r.score, 3),
                },
            )
            for r in results
        ]

    dispatcher.register(
        SubAgent(
            name="repo_search",
            source="knowledge_repo",
            search_fn=repo_search,
            timeout=5.0,
        )
    )

    policy = PolicyEngine()

    for name, connector in connectors.items():

        async def live_search(
            query: str,
            context: AuthContext,
            *,
            connector: BaseConnector = connector,
        ) -> list[Document]:
            documents = await connector.live_search(query)
            return [doc for doc in documents if policy.can_access_document(context, doc.metadata)]

        dispatcher.register(
            SubAgent(
                name=f"{name}_live",
                source=name,
                search_fn=live_search,
                timeout=10.0,
            )
        )

    return dispatcher


def main():
    """Main entry point — initialize and launch Grasp."""
    logger.info("Starting Grasp")

    try:
        settings = load_settings()
        logger.info(
            f"Configuration loaded ({len(settings.get_configured_connectors())} connectors configured)"
        )
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        logger.error("  Copy .env.example to .env and fill in your credentials")
        sys.exit(1)

    db_engine = create_engine(settings.database_url)
    database_target = (
        settings.database_url.split("@")[-1] if "@" in settings.database_url else "local"
    )
    logger.info(f"Database engine created ({database_target})")

    connectors = build_connectors(settings)

    repo_manager = RepoManager(
        repo_path=settings.repo_path,
        anthropic_api_key=settings.anthropic_api_key,
        classifier_model=settings.classifier_model,
        remote_url=settings.github_remote_url,
        github_pat=settings.github_pat,
    )
    logger.info(f"Repository manager initialized at {settings.repo_path}")

    vector_store = VectorStore(
        persist_dir=settings.chroma_path,
        openai_api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
    )
    logger.info(f"Vector store initialized ({vector_store.document_count} chunks indexed)")

    checkpoint_manager = CheckpointManager(engine=db_engine)
    logger.info("Checkpoint manager initialized")

    state_dir = settings.repo_path / ".grasp_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    audit_store = PostgresAuditStore(db_engine)
    change_set_service = ChangeSetService(
        engine=db_engine,
        repository=repo_manager,
        search_index=vector_store,
        state_dir=state_dir,
        audit_store=audit_store,
    )
    logger.info("Revisioned knowledge service initialized")
    orchestrator = SyncOrchestrator(
        connectors=connectors,
        repo_manager=repo_manager,
        change_sets=change_set_service,
        checkpoints=checkpoint_manager,
        engine=db_engine,
        overlap_seconds=settings.sync_overlap_seconds,
    )
    logger.info("Sync orchestrator initialized")

    job_queue = PostgresJobQueue(
        db_engine,
        poll_seconds=settings.worker_poll_seconds,
        concurrency=settings.worker_concurrency,
    )

    async def run_sync_job(_payload):
        result = await orchestrator.run_sync()
        if "error" in result:
            raise RuntimeError(result["error"])
        try:
            await agent_service.enqueue_event(
                "knowledge_sync",
                "default",
                event_id=str(_payload.get("_job_id") or uuid.uuid4()),
            )
        except Exception:
            logger.exception("Knowledge sync completed, but agent event fan-out failed")

    job_queue.register("sync", run_sync_job)

    async def rebuild_index_job(_payload):
        await change_set_service.rebuild_from_git("default")

    job_queue.register("rebuild-index", rebuild_index_job)

    metrics = MetricRecorder()
    scheduler = SyncScheduler(
        orchestrator=orchestrator,
        hours=settings.sync_cron_hours,
        minute=settings.sync_cron_minute,
        job_queue=job_queue,
        metrics=metrics,
    )

    query_shortener = QueryShortener(
        anthropic_api_key=settings.anthropic_api_key,
        model=settings.query_shortener_model,
        system_prompt=settings.query_shortener_system_prompt,
    )
    logger.info(f"Query shortener initialized (model: {settings.query_shortener_model})")

    provider_router = None
    if settings.provider_routing:
        provider_router = ProviderRouter(
            [ConnectorProvider(connector) for connector in connectors.values()],
            max_providers=settings.max_live_providers,
        )
    dispatcher = build_sub_agent_dispatcher(
        connectors, vector_store, query_shortener, provider_router
    )
    tool_executor = ToolExecutor(
        dispatcher=dispatcher,
        vector_store=vector_store,
        repo_manager=repo_manager,
        connectors=connectors,
    )
    context_router = (
        ContextRouter(repo_manager, token_budget=settings.context_token_budget)
        if settings.context_routing
        else None
    )
    query_engine = QueryEngine(
        anthropic_api_key=settings.anthropic_api_key,
        model=settings.agent_model,
        tool_executor=tool_executor,
        context_router=context_router,
        metrics=metrics,
    )
    logger.info(f"Query engine initialized (model: {settings.agent_model})")

    agent_service = AgentService(
        db_engine,
        query_engine=query_engine,
        job_queue=job_queue,
        enabled=settings.agents_enabled,
        audit=audit_store,
        metrics=metrics,
    )

    async def run_agent_job(payload):
        await agent_service.execute_job(dict(payload))

    job_queue.register("agent-run", run_agent_job)
    agent_scheduler = AgentScheduler(agent_service)
    logger.info(
        "Company-brain agent service initialized (%s)",
        "enabled" if settings.agents_enabled else "disabled",
    )

    contribution_manager = ContributionManager(
        engine=db_engine,
        repo_manager=repo_manager,
        change_sets=change_set_service,
        state_dir=state_dir,
    )
    logger.info("Contribution manager initialized")

    user_manager = UserManager(
        engine=db_engine,
        session_secret=settings.effective_session_secret,
        google_client_id=settings.google_client_id,
        session_max_age=settings.access_token_max_age_seconds,
    )
    logger.info("User manager initialized")

    chat_manager = ChatManager(engine=db_engine)
    logger.info("Chat manager initialized")

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
        chat_manager=chat_manager,
        google_client_id=settings.google_client_id,
        change_set_service=change_set_service,
        policy_engine=PolicyEngine(),
        trusted_origins=settings.trusted_origins,
        upload_max_bytes=settings.upload_max_bytes,
        upload_max_pages=settings.upload_max_pages,
        upload_max_text_chars=settings.upload_max_text_chars,
        auth_rate_limit=settings.auth_rate_limit_per_minute,
        query_rate_limit=settings.query_rate_limit_per_minute,
        upload_rate_limit=settings.upload_rate_limit_per_minute,
        job_queue=job_queue,
        metrics=metrics,
        agent_service=agent_service,
        agent_scheduler=agent_scheduler,
    )

    worker_task = None

    @app.on_event("startup")
    async def on_startup():
        nonlocal worker_task
        await init_db(db_engine)
        reconciliation = await change_set_service.reconcile()
        if reconciliation["repaired"]:
            logger.warning("Reconciled %s interrupted change sets", reconciliation["repaired"])
        if vector_store.needs_rebuild():
            await job_queue.enqueue(
                "rebuild-index",
                {"organization_id": "default"},
                idempotency_key=(
                    f"rebuild-index:{repo_manager.current_commit()}:"
                    f"{vector_store.INDEX_SCHEMA_VERSION}"
                ),
            )
        loop = asyncio.get_running_loop()
        scheduler.start(loop=loop)
        await agent_scheduler.start(loop)
        worker_task = asyncio.create_task(job_queue.run_forever())
        logger.info("Schedulers and durable worker started")

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler.stop()
        agent_scheduler.stop()
        job_queue.stop()
        if worker_task:
            await worker_task
        for connector in connectors.values():
            if hasattr(connector, "close"):
                await connector.close()
        await db_engine.dispose()
        logger.info("Shutdown complete")

    logger.info(f"Starting server on {settings.host}:{settings.port}")
    logger.info(f"Dashboard: http://localhost:{settings.port}")

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
