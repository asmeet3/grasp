<p align="center">
  <img src="src/logos/grasp-invert-transparent.png" alt="Grasp logo" width="30%">
</p>

Grasp is a self-hosted institutional knowledge assistant. It syncs content from Confluence, Jira, SharePoint, Slack, and Notion into a local Git-backed Markdown repository and a persistent ChromaDB index, then uses Anthropic Claude to answer questions with links to the source material.

The application includes a browser chat experience, account approval and role management, persistent per-user chat history, a contribution workflow, scheduled connector syncs, governed company-brain agents, rate limiting, append-only audit logging, observability metrics, and an admin dashboard for reviewing knowledge-repository changes before they are committed or pushed.

## Features

- **Permission-aware hybrid retrieval:** every query carries an authenticated organization, role, permissions, and ACL principals. Optional provider routing avoids live calls when committed evidence is sufficient.
- **Agentic follow-up:** Claude can fetch full repository documents, run filtered semantic searches, or issue targeted live searches when the initial snippets are insufficient.
- **Five connectors:** Confluence Cloud, Jira Cloud, SharePoint Online, Slack, and Notion.
- **Resumable synchronization:** the first sync is checkpointed; later runs are incremental. A failed connector receives a full retry on the next mixed sync.
- **Git-backed knowledge store:** documents are normalized to Markdown, classified into six knowledge types, and recorded with YAML frontmatter and generated indexes.
- **Revisioned review:** syncs and contributions create immutable, isolated change sets. Rejected proposals never enter Git or Chroma.
- **Streaming chat:** answers are sent to the browser as Server-Sent Events (SSE), rendered as Markdown, and can be stopped by the user.
- **Conversation context:** up to the latest 10 user/assistant pairs from the current thread are supplied to Claude. Authenticated users' chat threads are stored in PostgreSQL.
- **Authentication and authorization:** short-lived signed sessions, separate job titles and security roles, default-deny document ACLs, per-endpoint rate limits, and an admin key restricted to bootstrap user administration.
- **User contributions:** users can submit text, code, Markdown, TXT, PDF, or DOCX content for admin review.
- **Company-brain agents:** declarative, governed, read-only agents with scheduling, event triggers, daily token budgets, concurrency limits, and organization-wide emergency stop.
- **Durable job queue:** PostgreSQL-backed workers with leases, idempotency keys, exponential backoff, and dead-letter capture for failed jobs.
- **Append-only audit trail:** all agent runs, change-set actions, and administrative mutations are recorded in a structured audit log.
- **Observability metrics:** in-process thread-safe metric recorder tracks latencies, costs, and rates. An authenticated `/api/admin/metrics` endpoint exposes distribution snapshots.
- **Secret redaction:** log output is filtered to redact API keys, tokens, and secrets before they reach handlers.
- **Rate limiting:** per-IP sliding-window rate limits on authentication, queries, and file uploads protect the service from abuse.
- **Operations dashboard:** connector health, sync state/history, pending Git changes, contributions, agents, and user approvals are available at `/admin`.

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
        v
Isolated immutable change set (base commit + file operations + provenance)
        |
        +--> reject: mark rejected; active Git/index remain unchanged
        `--> approve: validate base --> commit --> optional push --> green index
                                      --> verify manifest --> atomic activation
```

Connectors run concurrently. The first successful run is a full sync, using PostgreSQL checkpoints after each connector batch. Later runs resume from the prior run's start watermark with an overlap window and content-hash deduplication, so changes made during a long sync are not skipped. Jobs use PostgreSQL leases and idempotency keys.

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

5. Register the first user, open `/admin`, and enter `ADMIN_KEY` (no prior sign-in is required). Approving the first account through this bootstrap view grants it the administrator system role. Sign in with that account, then trigger the first sync. When it finishes, review the pending repository changes before approving the Git commit/push.

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

The database schema is bootstrapped automatically at startup via `metadata.create_all()`.

## Configuration

Settings are loaded from environment variables and `.env` by `src/config.py`.

### Core settings

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `ANTHROPIC_API_KEY` | Yes | - | Claude queries, query shortening, and document classification |
| `ADMIN_KEY` | Yes | - | Constant-time checked bootstrap key for initial user administration |
| `DATABASE_URL` | No | `postgresql+asyncpg://grasp:grasp@localhost:5432/grasp` | Async PostgreSQL connection URL; a reachable PostgreSQL server is still required |
| `SESSION_SECRET` | Recommended | Falls back to `ADMIN_KEY` | Signs short-lived user access tokens |
| `ACCESS_TOKEN_MAX_AGE_SECONDS` | No | `3600` | Access-token lifetime (300–86400) |
| `TRUSTED_ORIGINS` | No | Local app origins | Exact CORS origin allowlist |
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

Approval always creates a local commit. If a remote is configured, the exact approved commit is pushed idempotently before indexing. Push or indexing failures leave the previous searchable revision active and the change set retryable.

### Sync, server, and rate limits

| Variable | Default | Purpose |
|---|---|---|
| `SYNC_CRON_HOURS` | `[2,5,8,11,14]` | Hours passed to the APScheduler cron trigger |
| `SYNC_CRON_MINUTE` | `30` | Minute within each configured hour |
| `SYNC_BATCH_SIZE` | `100` | Connector documents processed per batch where supported |
| `HOST` | `0.0.0.0` | Uvicorn bind address |
| `PORT` | `8000` | Uvicorn port |
| `GOOGLE_CLIENT_ID` | Empty | Enables Google registration/sign-in when set |
| `SYNC_OVERLAP_SECONDS` | `300` | Incremental-sync overlap window |
| `UPLOAD_MAX_BYTES` | `10485760` | Server-enforced upload limit |
| `UPLOAD_MAX_PAGES` | `200` | PDF page limit |
| `UPLOAD_MAX_TEXT_CHARS` | `2000000` | Extracted-text limit |
| `WORKER_CONCURRENCY` | `4` | Concurrent durable queue workers for sync, indexing, and agent runs |
| `WORKER_POLL_SECONDS` | `1.0` | Durable queue polling interval (0.1–60.0) |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `20` | Per-IP rate limit on authentication endpoints |
| `QUERY_RATE_LIMIT_PER_MINUTE` | `60` | Per-user rate limit on the query endpoint |
| `UPLOAD_RATE_LIMIT_PER_MINUTE` | `10` | Per-user rate limit on contribution/upload endpoints |

### Rollout flags

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_REQUIRED` | `true` | Require authentication for all non-public endpoints |
| `REVISIONED_KNOWLEDGE` | `true` | Require change-set review before knowledge enters Git or Chroma |
| `CONTEXT_ROUTING` | `false` | Inject canonical company and domain context into queries |
| `STRUCTURED_MEMORY` | `false` | Enable the typed, ACL-governed entity/relationship store |
| `PROVIDER_ROUTING` | `false` | Select live providers based on query relevance instead of calling all |
| `AGENTS_ENABLED` | `true` | Enable governed, read-only company-brain agents |
| `SELF_IMPROVEMENT_ENABLED` | `false` | Enable evaluation-gated self-improvement proposals |
| `CONTEXT_TOKEN_BUDGET` | `8000` | Token budget for canonical context injection |
| `MAX_LIVE_PROVIDERS` | `2` | Maximum live providers selected per query when routing is enabled |

Governed, read-only company-brain agents default to true; self-improvement remains disabled. Set `AGENTS_ENABLED=false` and restart to disable all agent execution. Validation enforces safe rollout ordering: agents and self-improvement require both authentication and revisioned knowledge.

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

`job_title` is organization metadata. `system_role` is one of `member`, `knowledge_editor`, `operator`, or `administrator` and determines permissions. Existing `reviewer` assignments are migrated to `knowledge_editor`. Password changes increment a password version and invalidate all prior sessions.

The browser stores a local cache of up to 30 chat threads and synchronizes authenticated threads to PostgreSQL. Chat context is isolated per thread. Profile settings support name and date-of-birth updates, email-account password changes, profile images, and account deletion.

### Security roles and permissions

Permissions are checked by a central default-deny `PolicyEngine`. Each system role grants a fixed permission set:

| System role | Permissions |
|---|---|
| `member` | `query`, `contribute` |
| `knowledge_editor` | `query`, `contribute`, `review` |
| `operator` | `query`, `contribute`, `review`, `manage_agents`, `view_audit` |
| `administrator` | All permissions including `manage_users` |

Document-level access is enforced through ACL principals that must intersect with the authenticated user's principals. Documents with no explicit principals are inaccessible by design. Agent runs inherit the owner's ACL scope and may further narrow — but never broaden — access through allowed domains and classification levels.

### Sync review

Scheduled or manually triggered syncs normalize and validate content into an isolated change set. Active repository files and Chroma remain untouched until review.

- **Approve:** validate the proposal base, replay operations, regenerate derived JSON indexes, commit/push, build a green Chroma collection, verify hashes and chunk counts, and activate it.
- **Reject:** mark only the isolated proposal rejected.

Git, PostgreSQL, and Chroma are coordinated as an idempotent saga. Startup reconciliation makes interrupted states retryable, and full indexes can be rebuilt from committed Markdown.

### Contributions

Authenticated users can submit text/code or upload `.txt`, `.md`, `.pdf`, and `.docx` files. PDF and DOCX text is extracted on the server; the original upload is stored under `knowledge_repo/.grasp_state/contributions/`.

Knowledge editors, operators, and administrators can edit, approve, or reject a pending submission. Approval runs the same commit-index-activate pipeline as connector synchronization.

### Company-brain agents

Operators and administrators can manage agents from the **Agents** screen in `/admin`.
An agent is a declarative, read-only routine with an owner, role, purpose, domain and
classification scope, analysis skill, runtime limit, daily token budget, concurrency
limit, escalation path, and optional UTC cron schedule. It can also run after a
successful knowledge sync.

Built-in analysis skills:

| Skill | Purpose |
|---|---|
| `knowledge_brief` | Synthesize relevant facts into a concise brief with citations |
| `gap_analysis` | Identify missing, contradictory, stale, or weakly supported knowledge |
| `risk_watch` | Surface material risks, blockers, dependencies, and unowned follow-ups |
| `decision_digest` | Summarize decisions, rationale, owners, dates, and unresolved questions |

Every run uses the same query engine and policy enforcement as interactive chat. The
agent inherits the approved owner's document ACLs and may narrow—but never broaden—
that access. Runs are queued durably, leased, audited, retained in PostgreSQL, and
automatically paused after three consecutive failures. Identical consecutive reports
can be suppressed. An organization-wide persistent emergency stop prevents new runs.
Agents are read-only and cannot execute external actions.

### Durable job queue

All background work — syncs, index rebuilds, and agent runs — is managed through a
PostgreSQL-backed durable job queue. Workers claim jobs with time-limited leases,
retry failures with exponential backoff, and move permanently failing jobs to a
dead-letter table after a configurable number of attempts (default 5). Idempotency
keys prevent duplicate work, and `SKIP LOCKED` ensures safe concurrent polling by
multiple workers.

### Audit trail

Grasp maintains an append-only `audit_events` table in PostgreSQL. Events cover:
agent creation, activation, pausing, run lifecycle (started, completed, suppressed,
failed), change-set operations, and emergency-stop toggling.
Each event records the actor, organization, resource type, resource ID, and
structured details. The `VIEW_AUDIT` permission (operator or administrator) is
required to access audit data.

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
| `POST` | `/api/query` | `query` permission | Stream an answer as SSE; accepts `question` and optional `history` |
| `GET` | `/api/status` | `query` permission | System state, connector health, sync timing, repository counts, and vector statistics |
| `GET` | `/api/sources` | `query` permission | Counts grouped by source and knowledge type |
| `GET` | `/api/health/live` | Public | Minimal process liveness only |

The browser attaches the bearer token to query, status, contribution, chat, and review requests.

### Administration

Sync/change/contribution review endpoints require the `review` permission. User bootstrap endpoints accept an administrator session or `X-Admin-Key`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/bootstrap/status` | Report whether first-run administrator bootstrap is still required |
| `POST` | `/api/admin/bootstrap/claim` | Promote a signed-in user while the one-time bootstrap window is open |
| `GET` | `/api/admin/access` | Validate an operations session or one-time administrator bootstrap |
| `GET` | `/api/admin/users` | List registered users |
| `POST` | `/api/admin/users/{id}/approve` | Approve a user and assign a role |
| `POST` | `/api/admin/users/{id}/reject` | Reject or revoke a user |
| `PUT` | `/api/admin/users/{id}/role` | Change an approved user's role |
| `GET` | `/api/admin/metrics` | Read observability metric distributions (`view_audit` permission) |
| `POST` | `/api/sync/trigger` | Start a background sync |
| `GET` | `/api/sync/status` | Read current workers and sync progress |
| `GET` | `/api/sync/history` | Return up to 100 stored sync runs |
| `GET` | `/api/changes/pending` | Get the pending Git change summary |
| `GET` | `/api/changes/diff/{path}` | View a tracked diff or generated new-file diff |
| `POST` | `/api/changes/approve` | Commit pending changes and optionally push |
| `POST` | `/api/changes/reject` | Revert all pending repository changes |
| `GET` | `/api/contributions/pending` | List pending contributions |
| `GET` | `/api/contributions/count` | Count pending contributions |
| `GET` | `/api/contributions/{id}` | Read one contribution |
| `PUT` | `/api/contributions/{id}` | Edit a pending contribution |
| `POST` | `/api/contributions/{id}/approve` | Approve and write a contribution |
| `POST` | `/api/contributions/{id}/reject` | Reject a contribution |

Agent endpoints require the `manage_agents` permission (Operator or Administrator).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agents` | List definitions and latest-run state |
| `POST` | `/api/agents` | Create an inactive agent definition |
| `GET` | `/api/agents/{id}` | Read one agent definition |
| `PUT` | `/api/agents/{id}` | Update an agent definition |
| `PUT` | `/api/agents/{id}/activation` | Activate or pause an agent |
| `POST` | `/api/agents/{id}/run` | Queue a manual run |
| `GET` | `/api/agents/runs` | List retained run history |
| `GET` | `/api/agents/runs/{id}` | Read a run and its report |
| `GET` | `/api/agents/status` | Read feature and emergency-stop state |
| `PUT` | `/api/agents/emergency-stop` | Stop or resume organization agent execution |
| `GET` | `/api/agents/owners` | List approved users eligible to own agents |
| `GET` | `/api/agents/templates` | List built-in analysis skills and schedules |

### Contribution submission

| Method | Path | Protection | Purpose |
|---|---|---|---|
| `POST` | `/api/contributions/submit` | `contribute` permission | Submit text, code, or a document body |
| `POST` | `/api/contributions/upload` | `contribute` permission | Upload TXT, Markdown, PDF, or DOCX |
| `GET` | `/api/contributions/my` | `contribute` permission | List submissions by authenticated user ID |
| `GET` | `/api/contributions/{id}/download` | Owner or knowledge editor | Download an original uploaded file |

Submitter identity always comes from the authenticated user ID; display names are not authorization identities.

## Knowledge repository layout

```text
knowledge_repo/
  .grasp_state/              Isolated proposals and uploaded originals (Git-ignored)
  company/                   Canonical company context and policies
  domains/<domain>/          Canonical domain context
  agents/                    Versioned declarative agent definitions
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

Raw source paths include a stable external-ID hash, and same-title knowledge documents are collision-safe. Domain is orthogonal to the existing six knowledge types.

## Project structure

```text
main.py                        Composition root and Uvicorn entry point
src/
  config.py                    Pydantic environment settings
  database.py                  PostgreSQL schema and initialization
  auth.py                      Accounts, sessions, Google auth, and roles
  chat_manager.py              Per-user PostgreSQL chat persistence
  contributions.py             Submission and review workflow
  changesets.py                Immutable knowledge proposals and commit-index-activate saga
  ingestion.py                 Connector-neutral ingestion records and adapters
  jobs.py                      PostgreSQL-backed idempotent job queue with leases
  agents.py                    Governed company-brain agent definitions, runs, and scheduling
  audit.py                     Append-only audit event persistence
  observability.py             Metric recorder and secret log redaction filter
  context_router.py            Token-budgeted canonical company/domain context selection
  providers.py                 Capability-based live provider routing
  improvement.py               Evaluation-gated self-improvement proposals
  memory.py                    Typed, ACL-governed organizational memory (entities/relationships)
  api/
    server.py                  FastAPI routes, static pages, and SSE endpoint
    models.py                  Pydantic request/response models
    security.py                Sliding-window rate limiter
  agent/
    engine.py                  Claude coordinator and conversation handling
    query_shortener.py         Live-search query decomposition
    sub_agents.py              Parallel repository/live search dispatcher
    tools.py                   Coordinator tool definitions and execution
  connectors/                  Confluence, Jira, SharePoint, Slack, and Notion clients
    base.py                    Abstract connector interface and Document model
  core/
    security.py                AuthContext, PolicyEngine, permissions, and system roles
    changes.py                 Change-set state machine and invariants
    interfaces.py              Protocol-based architectural boundaries
  index/vector_store.py        ChromaDB indexing, chunking, and semantic search
  repo/manager.py              Repository layout, classification, Git review, and indexes
  sync/                        Scheduler, orchestration, and PostgreSQL checkpoints
    orchestrator.py            Concurrent connector execution and change-set creation
    scheduler.py               APScheduler-based cron schedule
    checkpoints.py             Resumable sync state in PostgreSQL
  static/                      Chat, login, and admin HTML/CSS/JavaScript
  icons/                       Application icons
  logos/                       Application logos
Dockerfile                     Python 3.12 application image
docker-compose.yml             Application, PostgreSQL, health checks, and volumes
pyproject.toml                 Package metadata and dependencies
tests/                         Pytest test suite
.github/workflows/ci.yml       GitHub Actions CI pipeline
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
| Input sanitization | bleach |
| Frontend | Static HTML, CSS, and JavaScript served by FastAPI |

## CI pipeline

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push and pull request against a service PostgreSQL container:

```text
ruff check .                Lint
ruff format --check .       Formatting
mypy                        Type checking (strict on core modules)
pytest --cov                Tests with branch coverage (≥70%)
bandit -q -r src            Security analysis (excluding connectors)
pip check                   Dependency consistency
```

## Development checks

Run the deterministic unit/security suite and quality gates with:

```powershell
ruff check .
ruff format --check .
mypy
pytest --cov --cov-report=term-missing
bandit -q -r src -x src/connectors
```

Install the optional development dependencies with `python -m pip install -e ".[dev]"` before running Ruff or adding pytest coverage.

## License

MIT
