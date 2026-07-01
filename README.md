# Grasp

Grasp is a self-hosted knowledge retrieval system that connects to your company's Confluence, Jira, SharePoint, Slack, and Notion instances. It periodically syncs content from these platforms into a local Git repository and a ChromaDB vector index, then uses Claude to answer natural-language questions about that content.

At query time, Grasp searches both the local index and the live platform APIs in parallel, then synthesizes a single answer with source citations.

## How It Works

Grasp has two main operating modes:

**Sync** — A background scheduler pulls documents from all configured platforms on a cron schedule (configurable, defaults to five times per day). Documents are classified into one of ten information types using Claude Haiku, written to a Git repository as Markdown files with YAML frontmatter, and indexed into ChromaDB. The initial sync supports checkpointing so it can resume if interrupted. Subsequent syncs are incremental, fetching only documents modified since the last run. After each sync, changes are staged for human review before being committed and pushed.

**Query** — When a user asks a question, Grasp fans out search requests to the local vector index and all configured platform APIs concurrently. A coordinator agent (Claude Sonnet) receives the aggregated results and synthesizes an answer. If the initial results are insufficient, the agent can make up to four follow-up tool calls to read specific files, run filtered searches, or query individual platforms directly. Responses are streamed to the client via SSE.

## Requirements

- Python 3.11+
- PostgreSQL database
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

3. Open `http://localhost:8000` for the query interface, `http://localhost:8000/admin` for the admin dashboard, or `http://localhost:8000/login` for the login/registration page.

## Configuration

All configuration is done through environment variables in `.env`. See `.env.example` for the full list.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `ADMIN_KEY` | Secret key for admin API endpoints |
| `DATABASE_URL` | PostgreSQL connection URL (e.g. `postgresql+asyncpg://grasp:grasp@localhost:5432/grasp`) |

### Optional — Authentication

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Client ID (leave empty to disable Google sign-in) |
| `SESSION_SECRET` | Secret for signing session tokens (falls back to `ADMIN_KEY` if not set) |

### Optional — Git Remote

| Variable | Description |
|---|---|
| `GITHUB_REPO_PATH` | Local path for the knowledge repo (default: `./knowledge_repo`) |
| `GITHUB_REMOTE_URL` | Remote Git URL for pushing committed changes |
| `GITHUB_PAT` | GitHub Personal Access Token, injected into the HTTPS remote URL |

### Optional — Platform Connectors

Configure only the platforms you use. Grasp auto-detects which connectors are available based on which credentials are present.

| Variable | Platform |
|---|---|
| `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` | Confluence |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Jira |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_ID` | SharePoint |
| `SLACK_BOT_TOKEN` | Slack |
| `NOTION_API_KEY` | Notion |

### Optional — Sync Schedule

| Variable | Default | Description |
|---|---|---|
| `SYNC_CRON_HOURS` | `[2,5,8,11,14]` | Hours (UTC) to run sync |
| `SYNC_CRON_MINUTE` | `30` | Minute within each hour |
| `SYNC_BATCH_SIZE` | `100` | Documents per batch during sync |

### Optional — Server

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server bind port |

## API

### Authentication Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/register` | No | Register via email and password |
| `POST` | `/api/auth/register/google` | No | Register or login via Google |
| `POST` | `/api/auth/login` | No | Login via email and password |
| `POST` | `/api/auth/login/google` | No | Login via Google ID token |
| `GET` | `/api/auth/me` | Session | Get current user profile |
| `GET` | `/api/auth/config` | No | Return public auth config (Google enabled, client ID) |
| `PUT` | `/api/auth/profile` | Session | Update profile (name, DOB, profile picture) |
| `PUT` | `/api/auth/password` | Session | Change password (invalidates all sessions) |
| `DELETE` | `/api/auth/account` | Session | Permanently delete account |

### Knowledge & Query Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/query` | No | Submit a question; returns an SSE stream |
| `GET` | `/api/status` | No | System status, connector health, document counts |
| `GET` | `/api/sources` | No | Document counts grouped by source and type |

### Sync & Change Management (Admin)

All admin endpoints require the `X-Admin-Key` header.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/sync/trigger` | Admin | Trigger a sync manually |
| `GET` | `/api/sync/status` | Admin | Current sync progress and worker status |
| `GET` | `/api/sync/history` | Admin | Past sync results |
| `GET` | `/api/changes/pending` | Admin | Pending changeset awaiting approval |
| `GET` | `/api/changes/diff/{path}` | Admin | Git diff for a specific pending file |
| `POST` | `/api/changes/approve` | Admin | Commit and push pending changes |
| `POST` | `/api/changes/reject` | Admin | Revert all pending changes |

### User Management (Admin)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/admin/users` | Admin | List all registered users |
| `POST` | `/api/admin/users/{id}/approve` | Admin | Approve a pending user and assign a role |
| `POST` | `/api/admin/users/{id}/reject` | Admin | Reject a pending user |
| `PUT` | `/api/admin/users/{id}/role` | Admin | Change an approved user's role |

### Contributions

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/contributions/submit` | No | Submit a text/document contribution |
| `POST` | `/api/contributions/upload` | No | Upload a file (.txt, .md, .pdf, .docx) as a contribution |
| `GET` | `/api/contributions/my` | No | List contributions for the current submitter |
| `GET` | `/api/contributions/pending` | Admin | List all pending contributions |
| `GET` | `/api/contributions/count` | Admin | Count of pending contributions |
| `GET` | `/api/contributions/{id}` | Admin | Get a single contribution |
| `PUT` | `/api/contributions/{id}` | Admin | Edit contribution content before approval |
| `GET` | `/api/contributions/{id}/download` | No | Download the original uploaded file |
| `POST` | `/api/contributions/{id}/approve` | Admin | Approve and write to the knowledge repo |
| `POST` | `/api/contributions/{id}/reject` | Admin | Reject a contribution |

## Authentication & Access Control

Grasp has a full user authentication system backed by PostgreSQL.

- **Registration**: Users can sign up with email/password or via Google OAuth. New accounts are held in `pending_approval` status until an admin approves them.
- **Roles**: Admins assign one of ten predefined roles on approval (Intern, Junior Associate, Associate, Senior Associate, Team Lead, Manager, Director, Principal, Vice President, Partner).
- **Sessions**: Authenticated via signed bearer tokens (7-day expiry). Password changes invalidate all existing sessions.
- **Google sign-in**: Enabled when `GOOGLE_CLIENT_ID` is set. The `GET /api/auth/config` endpoint exposes this to the frontend.

## Contributions

Users can submit content to be added to the knowledge repository without needing connector access.

- Submit text, code snippets, or upload files (.txt, .md, .pdf, .docx). PDFs and DOCX files are parsed server-side with text extraction.
- Submitted contributions enter a `pending` queue visible in the admin dashboard.
- Admins can edit the content, add notes, then approve or reject. Approved contributions are classified by Claude Haiku and written into the Git-backed knowledge repo.

## Knowledge Repository Layout

The repository uses a three-layer structure managed automatically by Grasp:

```
knowledge_repo/
  sources/          Raw ingestion — append-only, immutable after write
    confluence/
    jira/
    slack/
    docs/
    emails/
    meetings/
  knowledge/        Structured, curated knowledge units
    decisions/      ADRs, meeting notes, RFCs, design reviews
    projects/       Feature specs, PRDs, user stories, epics
    processes/      Runbooks, SOPs, deployment guides, test plans
    products/       Product areas, roadmaps, strategy, OKRs
    people/         Expertise profiles (opt-in)
    topics/         Cross-cutting themes — architecture, incidents, discussions, references
  teams/            Team-scoped spaces
  _index/           Auto-generated retrieval layer (graph, tags, embeddings, freshness)
  _schema/          Frontmatter schemas and source-connector configs
```

Each file includes YAML frontmatter with the document's ID, source platform, title, URL, classification, and last-updated timestamp. Claude Haiku classifies incoming documents into one of the six `knowledge/` categories at ingest time.

## Project Structure

```
main.py                          Entry point
src/
  config.py                      Pydantic settings loaded from .env
  database.py                    Async PostgreSQL via SQLAlchemy (users, contributions, sync state, checkpoints)
  auth.py                        User registration, login, session management, and Google OAuth
  contributions.py               User contribution submission, review, and approval workflow
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
    login.html                   Login and registration page
    styles.css                   Shared styles
    app.js                       Query interface logic
    admin.js                     Admin dashboard logic
    login.js                     Login/registration logic
```

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Anthropic Claude (Sonnet 4.6 for queries, Haiku 4.6 for classification) |
| Vector database | ChromaDB |
| Relational database | PostgreSQL (async via SQLAlchemy + asyncpg) |
| Git operations | GitPython |
| Scheduler | APScheduler 3.x |
| HTTP server | FastAPI + Uvicorn |
| HTTP client | httpx (async) |
| Auth | bcrypt + itsdangerous (session tokens), Google OAuth |
| File parsing | PyPDF2 (PDF), python-docx (DOCX) |
| Frontend | HTML, CSS, JavaScript |
| Deployment | Docker |
