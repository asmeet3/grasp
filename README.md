<p align="center">
  <img src="src/logos/grasp-invert-transparent.png" alt="Grasp logo" width="35%">
</p>

Grasp is a self-hosted institutional knowledge assistant. It syncs content from Confluence, Jira, SharePoint, Slack, and Notion into a local Git-backed Markdown repository and a persistent ChromaDB index, then uses Anthropic Claude to answer questions with links to the source material.

The application includes a browser chat experience, account approval and role management, persistent per-user chat history, a contribution workflow, scheduled connector syncs, and an admin dashboard for reviewing knowledge-repository changes before they are committed or pushed.

## Features

- **Hybrid retrieval:** every question searches the historical ChromaDB index and recent content from all configured platform APIs in parallel.
- **Agentic follow-up:** Claude can fetch full repository documents, run filtered semantic searches, or issue targeted live searches when the initial snippets are insufficient.
- **Five connectors:** Confluence Cloud, Jira Cloud, SharePoint Online, Slack, and Notion.
- **Resumable synchronization:** the first sync is checkpointed; later runs are incremental. A failed connector receives a full retry on the next mixed sync.
- **Git-backed knowledge store:** documents are normalized to Markdown, classified into six knowledge types, and recorded with YAML frontmatter and generated indexes.
- **Human review:** sync output remains as pending Git working-tree changes until an administrator approves and commits it or rejects it.
- **Streaming chat:** answers are sent to the browser as Server-Sent Events (SSE), rendered as Markdown, and can be stopped by the user.
- **Conversation context:** up to the latest 10 user/assistant pairs from the current thread are supplied to Claude. Authenticated users' chat threads are stored in PostgreSQL.
- **Authentication:** email/password registration, optional Google sign-in, seven-day signed sessions, admin approval, profile settings, and ten assignable organization roles.
- **User contributions:** users can submit text, code, Markdown, TXT, PDF, or DOCX content for admin review.
- **Operations dashboard:** connector health, sync state/history, pending Git changes, contributions, and user approvals are available at `/admin`.

## How it works

### Sync and indexing

```text
Configured platform APIs
        |
        | full, incremental, or mixed sync
        v
Normalized Document (Markdown + metadata)
        |
        +--> Claude Haiku classification
        |       decisions | projects | processes
        |       products  | people   | topics
        |
        +--> knowledge_repo/sources/...       source-oriented copy
        +--> knowledge_repo/knowledge/...     classified copy
        +--> knowledge_repo/_index/...        graph, tags, people, freshness
        +--> chroma_data/                      chunked semantic index
        |
        v
Pending Git working-tree changes
        |
        +--> approve: git add + commit + optional push
        `--> reject: restore tracked files and clean untracked files
```

Connectors run concurrently. The first successful run is a full sync, using PostgreSQL checkpoints after each connector batch. Later runs request only documents changed since the previous sync timestamp. Sync history and checkpoint state are also stored in PostgreSQL.

Documents are split with a Markdown-aware separator hierarchy (headings, code fences, horizontal rules, paragraphs, lines, sentences, then words). Chunks target 1,500 characters with up to 200 characters of overlap. Search results are deduplicated by document ID so only the best matching chunk from each document is returned.

The vector store uses OpenAI's configured embedding model when `OPENAI_API_KEY` is set. Without it, ChromaDB's default `all-MiniLM-L6-v2` embedding function is used.

### Query flow

```text
Question
   |
   +--> original question --> ChromaDB semantic search
   |
   `--> Claude Haiku query shortening (1-3 queries)
            `--> configured live platform searches (last 4 hours)
   |
   v
Claude coordinator
   +--> cite and synthesize the retrieved sources
   +--> optionally read up to 5 full repository documents
   +--> optionally search the repository with source/type filters
   `--> optionally run a targeted platform search
   |
   v
SSE response stream
```

The repository search has a five-second timeout; each live connector search has a ten-second timeout. One connector failure does not prevent results from the others from reaching the coordinator.

## Requirements

- Python 3.11 or newer (the Docker image uses Python 3.12)
- PostgreSQL 16 or another compatible PostgreSQL server
- Git, because repository approval and rejection use Git operations
- An Anthropic API key
- An admin key chosen for this deployment
- Platform credentials for any connectors you want to enable
- Optional: an OpenAI API key for `text-embedding-3-large`; otherwise ChromaDB uses its local default embedding model

Grasp can start with no platform connectors, but syncing and live search will have no external sources. An existing Chroma index and its referenced Markdown files can still be queried.

## Quick start with Docker

1. Create the environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   On macOS or Linux, use `cp .env.example .env`.

2. At minimum, replace these values in `.env`:

   ```dotenv
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ADMIN_KEY=choose-a-strong-random-secret
   SESSION_SECRET=choose-another-strong-random-secret
   ```

   Clear the example Git remote and any connector credentials you are not using. `DATABASE_URL` is overridden to the Compose PostgreSQL service automatically.

3. Build and start the application and database:

   ```powershell
   docker compose up --build -d
   docker compose logs -f grasp
   ```

4. Open:

   - User application: <http://localhost:8000>
   - Login and registration: <http://localhost:8000/login>
   - Admin dashboard: <http://localhost:8000/admin>
   - Interactive API documentation: <http://localhost:8000/docs>

5. Register a user, enter `ADMIN_KEY` in the admin dashboard, approve the account with a role, and trigger the first sync. When it finishes, review the pending repository changes before approving the Git commit/push.

The Compose setup persists PostgreSQL, the knowledge repository, ChromaDB, and Grasp's internal state in named volumes.

## Run locally

1. Copy `.env.example` to `.env` and set the required secrets. Keep this database URL when PostgreSQL runs on the same machine:

   ```dotenv
   DATABASE_URL=postgresql+asyncpg://grasp:grasp@localhost:5432/grasp
   ```

2. Start PostgreSQL. The included Compose database can be used independently:

   ```powershell
   docker compose up -d db
   ```

3. Create and activate a virtual environment, then install Grasp:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .
   ```

   On macOS or Linux, activate with `source .venv/bin/activate`.

4. Run the server:

   ```powershell
   python main.py
   ```

Database tables are created automatically during application startup. There is no separate migration command in the current project.

## Configuration

Settings are loaded from environment variables and `.env` by `src/config.py`.

### Core settings

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `ANTHROPIC_API_KEY` | Yes | - | Claude queries, query shortening, and document classification |
| `ADMIN_KEY` | Yes | - | Value expected in `X-Admin-Key` for administrative API calls |
| `DATABASE_URL` | No | `postgresql+asyncpg://grasp:grasp@localhost:5432/grasp` | Async PostgreSQL connection URL; a reachable PostgreSQL server is still required |
| `SESSION_SECRET` | Recommended | Falls back to `ADMIN_KEY` | Signs seven-day user session tokens |
| `OPENAI_API_KEY` | No | Empty | Enables OpenAI embeddings instead of Chroma's default embedding model |
| `EMBEDDING_MODEL` | No | `text-embedding-3-large` | OpenAI embedding model used when an OpenAI key is present |

Keep the same embedding backend for an existing `chroma_data` directory. Changing between embedding models can produce incompatible vector dimensions; rebuild the Chroma data when deliberately changing backends.

### Claude models

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODEL` | `claude-sonnet-4-6` | Coordinates retrieval and writes answers |
| `CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Classifies synced documents into knowledge types |
| `QUERY_SHORTENER_MODEL` | `claude-haiku-4-5-20251001` | Converts a question into up to three live-search queries |
| `QUERY_SHORTENER_SYSTEM_PROMPT` | Built into `Settings` | Optional override for query-shortening behavior |

### Storage and Git

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_REPO_PATH` | `./knowledge_repo` | Local Git repository managed by Grasp |
| `GITHUB_REMOTE_URL` | Empty | Optional remote to configure as `origin` |
| `GITHUB_PAT` | Empty | Optional token inserted into an HTTPS remote URL |

Approval always creates a local commit. If a remote is configured, Grasp first pushes the current branch. If that push fails, it creates and pushes a `grasp/sync-<timestamp>` fallback branch. No pull request is created automatically.

### Sync and server

| Variable | Default | Purpose |
|---|---|---|
| `SYNC_CRON_HOURS` | `[2,5,8,11,14]` | Hours passed to the APScheduler cron trigger |
| `SYNC_CRON_MINUTE` | `30` | Minute within each configured hour |
| `SYNC_BATCH_SIZE` | `100` | Connector documents processed per batch where supported |
| `HOST` | `0.0.0.0` | Uvicorn bind address |
| `PORT` | `8000` | Uvicorn port |
| `GOOGLE_CLIENT_ID` | Empty | Enables Google registration/sign-in when set |

The Docker container normally runs in UTC, making the default schedule 02:30, 05:30, 08:30, 11:30, and 14:30 UTC (08:00, 11:00, 14:00, 17:00, and 20:00 IST). The scheduler does not explicitly set a timezone, so a local installation interprets these hours in the host timezone even though the application log labels them as UTC.

### Connectors

Only connectors with credentials present are initialized.

| Connector | Environment variables | Synced content |
|---|---|---|
| Confluence Cloud | `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` | Pages from accessible spaces; CQL is used for incremental and live search |
| Jira Cloud | `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Accessible issues, including metadata, descriptions, and comments; JQL is used for incremental and live search |
| SharePoint Online | `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_ID` | Files from site drives and non-hidden list items via Microsoft Graph |
| Slack | `SLACK_BOT_TOKEN` | Messages and thread replies from channels accessible to the bot |
| Notion | `NOTION_API_KEY` | Pages and database metadata shared with the integration, including recursively fetched page blocks |

Live searches are constrained to content updated in the last four hours. Connector access is also limited by the permissions of the supplied account, bot, app, or integration.

## Application workflows

### Accounts and chat

New email or Google accounts start in `pending_approval`. An administrator must approve the account and assign one of these roles before login succeeds: Intern, Junior Associate, Associate, Senior Associate, Team Lead, Manager, Director, Principal, Vice President, or Partner.

The role is organization metadata; admin API access is controlled separately by `ADMIN_KEY`. User sessions expire after seven days. Password changes increment a password version and invalidate all previous sessions.

The browser stores a local cache of up to 30 chat threads and synchronizes authenticated threads to PostgreSQL. Chat context is isolated per thread. Profile settings support name and date-of-birth updates, email-account password changes, profile images, and account deletion.

### Sync review

Scheduled or manually triggered syncs write repository files and update ChromaDB immediately. Grasp then records a pending change summary for review in the admin dashboard.

- **Approve:** stage every repository change, create a commit, and attempt the configured remote push.
- **Reject:** restore tracked repository files and remove untracked repository files, except `.grasp_state/`.

The Git decision and the vector index are not transactional: rejecting repository changes does not remove chunks that were already written to ChromaDB during the sync.

### Contributions

Authenticated users can submit text/code or upload `.txt`, `.md`, `.pdf`, and `.docx` files. PDF and DOCX text is extracted on the server; the original upload is stored under `knowledge_repo/.grasp_state/contributions/`.

Admins can edit, approve, or reject a pending submission. Approval classifies the content and writes it to the source and knowledge layers, where it becomes another pending Git change. In the current implementation, contribution approval does not directly index the new document in ChromaDB.

## API overview

FastAPI also exposes complete request/response schemas at `/docs` and `/openapi.json`.

### Authentication and chats

| Method | Path | Protection | Purpose |
|---|---|---|---|
| `POST` | `/api/auth/register` | Public | Register with email and password |
| `POST` | `/api/auth/register/google` | Public | Register or sign in with a Google ID token |
| `POST` | `/api/auth/login` | Public | Email/password login |
| `POST` | `/api/auth/login/google` | Public | Google login |
| `GET` | `/api/auth/config` | Public | Return whether Google sign-in is configured |
| `GET` | `/api/auth/me` | Bearer token | Return the current profile |
| `PUT` | `/api/auth/profile` | Bearer token | Update profile fields |
| `PUT` | `/api/auth/password` | Bearer token | Change password and invalidate sessions |
| `DELETE` | `/api/auth/account` | Bearer token | Delete the current account |
| `GET` | `/api/chats` | Bearer token | List the current user's chat threads |
| `POST` | `/api/chats` | Bearer token | Create or update a chat thread |
| `DELETE` | `/api/chats/{chat_id}` | Bearer token | Delete one owned chat thread |

Bearer tokens are sent as `Authorization: Bearer <token>`.

### Query and status

| Method | Path | Protection | Purpose |
|---|---|---|---|
| `POST` | `/api/query` | Public route | Stream an answer as SSE; accepts `question` and optional `history` |
| `GET` | `/api/status` | Public | System state, connector health, sync timing, repository counts, and vector statistics |
| `GET` | `/api/sources` | Public | Counts grouped by source and knowledge type |

The browser application redirects unauthenticated visitors to `/login`, but the `/api/query` route itself currently has no bearer-token dependency.

### Administration

All endpoints below require `X-Admin-Key: <ADMIN_KEY>`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/sync/trigger` | Start a background sync |
| `GET` | `/api/sync/status` | Read current workers and sync progress |
| `GET` | `/api/sync/history` | Return up to 100 stored sync runs |
| `GET` | `/api/changes/pending` | Get the pending Git change summary |
| `GET` | `/api/changes/diff/{path}` | View a tracked diff or generated new-file diff |
| `POST` | `/api/changes/approve` | Commit pending changes and optionally push |
| `POST` | `/api/changes/reject` | Revert all pending repository changes |
| `GET` | `/api/admin/users` | List registered users |
| `POST` | `/api/admin/users/{id}/approve` | Approve a user and assign a role |
| `POST` | `/api/admin/users/{id}/reject` | Reject or revoke a user |
| `PUT` | `/api/admin/users/{id}/role` | Change an approved user's role |
| `GET` | `/api/contributions/pending` | List pending contributions |
| `GET` | `/api/contributions/count` | Count pending contributions |
| `GET` | `/api/contributions/{id}` | Read one contribution |
| `PUT` | `/api/contributions/{id}` | Edit a pending contribution |
| `POST` | `/api/contributions/{id}/approve` | Approve and write a contribution |
| `POST` | `/api/contributions/{id}/reject` | Reject a contribution |

### Contribution submission

| Method | Path | Protection | Purpose |
|---|---|---|---|
| `POST` | `/api/contributions/submit` | Public; bearer optional | Submit text, code, or a document body |
| `POST` | `/api/contributions/upload` | Public; bearer optional | Upload TXT, Markdown, PDF, or DOCX |
| `GET` | `/api/contributions/my` | Public | Find submissions by query-string name or the `grasp_user` cookie |
| `GET` | `/api/contributions/{id}/download` | Public | Download an original uploaded file |

When a valid bearer token is supplied, the server uses the account's name instead of the submitted name.

## Knowledge repository layout

```text
knowledge_repo/
  .grasp_state/              Internal pending state and uploaded originals (Git-ignored)
  sources/                   Source-oriented Markdown copies
    confluence/YYYY-MM/
    jira/<project>/
    slack/YYYY-MM/
    docs/notion/
    docs/sharepoint/
    docs/user_contribution/
  knowledge/                 Classified Markdown copies
    decisions/
    projects/
    processes/
    products/
    people/
    topics/
      architecture/
      incidents/
      discussions/
      references/
      security/
      infrastructure/
      general/
  _index/                    Generated graph, tag, people, and freshness JSON
  _schema/                   Frontmatter and connector schema files
  teams/                     Reserved team-scoped spaces
```

Source and knowledge paths are derived from source-specific metadata, document titles, dates, project keys, and classification. A later sync can therefore rewrite an existing path; the Git history is the audit trail after changes are approved.

## Project structure

```text
main.py                        Composition root and Uvicorn entry point
src/
  config.py                    Pydantic environment settings
  database.py                  PostgreSQL tables and initialization
  auth.py                      Accounts, sessions, Google auth, and roles
  chat_manager.py              Per-user PostgreSQL chat persistence
  contributions.py             Submission and review workflow
  api/
    server.py                  FastAPI routes, static pages, and SSE endpoint
    models.py                  API request/response models
  agent/
    engine.py                  Claude coordinator and conversation handling
    query_shortener.py         Live-search query decomposition
    sub_agents.py              Parallel repository/live search dispatcher
    tools.py                   Coordinator tool definitions and execution
  connectors/                  Confluence, Jira, SharePoint, Slack, and Notion clients
  index/vector_store.py        ChromaDB indexing, chunking, and semantic search
  repo/manager.py              Repository layout, classification, Git review, and indexes
  sync/                        Scheduler, orchestration, and PostgreSQL checkpoints
  static/                      Chat, login, and admin HTML/CSS/JavaScript
Dockerfile                     Python 3.12 application image
docker-compose.yml             Application, PostgreSQL, health checks, and volumes
pyproject.toml                 Package metadata and dependencies
```

## Technology stack

| Area | Implementation |
|---|---|
| Coordinator/classifier | Anthropic Claude via the `anthropic` Python SDK |
| Embeddings | OpenAI embeddings or ChromaDB's default local embedding function |
| Vector database | Persistent ChromaDB with cosine distance |
| Relational data | PostgreSQL + SQLAlchemy asyncio + asyncpg |
| API/streaming | FastAPI, Uvicorn, and `sse-starlette` |
| Scheduling | APScheduler 3.x |
| Knowledge versioning | GitPython and the system Git executable |
| Connector HTTP | `httpx` with rate-limit retry support |
| Document conversion | Beautiful Soup, markdownify, PyPDF2, and python-docx |
| Authentication | bcrypt and itsdangerous; optional Google token verification |
| Frontend | Static HTML, CSS, and JavaScript served by FastAPI |

## Development checks

The repository currently has no committed automated test suite. Useful local checks are:

```powershell
python -m compileall main.py src
ruff check .
```

Install the optional development dependencies with `python -m pip install -e ".[dev]"` before running Ruff or adding pytest coverage.
