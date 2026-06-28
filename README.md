# Grasp (v1.6.2)

Grasp is a self-hosted knowledge retrieval system that connects to your company's Confluence, Jira, SharePoint, Slack, and Notion instances. It periodically syncs content from these platforms into a local Git repository and a ChromaDB vector index, then uses Claude to answer natural-language questions about that content.

At query time, Grasp searches both the local index and the live platform APIs in parallel, then synthesizes a single answer with source citations.

## How It Works

Grasp has two main operating modes:

**Sync** -- A background scheduler pulls documents from all configured platforms on a cron schedule (configurable, defaults to five times per day). Documents are classified into one of ten information types using Claude Haiku, written to a Git repository as Markdown files with YAML frontmatter, and indexed into ChromaDB. The initial sync supports checkpointing so it can resume if interrupted. Subsequent syncs are incremental, fetching only documents modified since the last run. After each sync, changes are staged for human review before being committed and pushed.

**Query** -- When a user asks a question, Grasp fans out search requests to the local vector index and all configured platform APIs concurrently. A coordinator agent (Claude Sonnet) receives the aggregated results and synthesizes an answer. If the initial results are insufficient, the agent can make up to four follow-up tool calls to read specific files, run filtered searches, or query individual platforms directly. Responses are streamed to the client via SSE.

## Requirements

- Python 3.11+
- An Anthropic API key
- Credentials for at least one supported platform (Confluence, Jira, SharePoint, Slack, or Notion)

## Setup

1. Copy the example environment file and fill in your credentials:

```
cp .env.example .env
```

2. Install and run locally:

```
pip install -e .
python main.py
```

Or use Docker:

```
docker-compose up -d
```

3. Open `http://localhost:8000` for the query interface, or `http://localhost:8000/admin` for the admin dashboard.

## Configuration

All configuration is done through environment variables in `.env`. See `.env.example` for the full list.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `ADMIN_KEY` | Secret key for admin API endpoints |

### Optional -- Git Remote

| Variable | Description |
|---|---|
| `GITHUB_REPO_PATH` | Local path for the knowledge repo (default: `./knowledge_repo`) |
| `GITHUB_REMOTE_URL` | Remote Git URL for pushing committed changes |
| `GITHUB_PAT` | GitHub Personal Access Token, injected into the HTTPS remote URL |

### Optional -- Platform Connectors

Configure only the platforms you use. Grasp auto-detects which connectors are available based on which credentials are present.

| Variable | Platform |
|---|---|
| `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` | Confluence |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Jira |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_ID` | SharePoint |
| `SLACK_BOT_TOKEN` | Slack |
| `NOTION_API_KEY` | Notion |

### Optional -- Sync Schedule

| Variable | Default | Description |
|---|---|---|
| `SYNC_CRON_HOURS` | `[2,5,8,11,14]` | Hours (UTC) to run sync |
| `SYNC_CRON_MINUTE` | `30` | Minute within each hour |
| `SYNC_BATCH_SIZE` | `100` | Documents per batch during sync |

### Optional -- Server

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server bind port |

## API

All admin endpoints require the `X-Admin-Key` header.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/query` | No | Submit a question; returns an SSE stream |
| `GET` | `/api/status` | No | System status, connector health, document counts |
| `GET` | `/api/sources` | No | Document counts grouped by source and type |
| `POST` | `/api/sync/trigger` | Admin | Trigger a sync manually |
| `GET` | `/api/sync/status` | Admin | Current sync progress and worker status |
| `GET` | `/api/sync/history` | Admin | Past sync results |
| `GET` | `/api/changes/pending` | Admin | Pending changeset awaiting approval |
| `GET` | `/api/changes/diff/{path}` | Admin | Git diff for a specific pending file |
| `POST` | `/api/changes/approve` | Admin | Commit and push pending changes |
| `POST` | `/api/changes/reject` | Admin | Revert all pending changes |

## Knowledge Repository Layout

Synced documents are stored as Markdown files organized by information type and source platform:

```
knowledge_repo/
  architecture/
    confluence/
    jira/
    ...
  features/
  operations/
  testing/
  decisions/
  strategy/
  incidents/
  discussions/
  references/
  general/
```

Each file includes YAML frontmatter with the document's ID, source platform, title, URL, classification, and last-updated timestamp.

## Project Structure

```
main.py                          Entry point
src/
  config.py                      Pydantic settings loaded from .env
  connectors/
    base.py                      BaseConnector interface, Document model, rate limiter
    confluence.py                Confluence REST API connector
    jira.py                      Jira REST API connector
    sharepoint.py                SharePoint (Microsoft Graph) connector
    slack.py                     Slack Web API connector
    notion.py                    Notion API connector
  sync/
    orchestrator.py              Parallel sync across all connectors
    scheduler.py                 APScheduler-based cron scheduling
    checkpoints.py               Resumable sync state persistence
  repo/
    manager.py                   Git repo management, document classification, change approval
  index/
    vector_store.py              ChromaDB indexing and semantic search
  agent/
    engine.py                    Claude-powered query coordinator with tool use
    sub_agents.py                Parallel search fan-out with timeout and error handling
    tools.py                     Tool definitions and executor for the coordinator agent
  api/
    server.py                    FastAPI application and route definitions
    models.py                    Pydantic request/response models
  static/
    index.html                   Query interface
    admin.html                   Admin dashboard
    styles.css                   Shared styles
    app.js                       Query interface logic
    admin.js                     Admin dashboard logic
```

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Anthropic Claude (Sonnet 4 for queries, Haiku 4 for classification) |
| Vector database | ChromaDB |
| Git operations | GitPython |
| Scheduler | APScheduler 3.x |
| HTTP server | FastAPI + Uvicorn |
| HTTP client | httpx (async) |
| Frontend | HTML, CSS, JavaScript |
| Deployment | Docker |
