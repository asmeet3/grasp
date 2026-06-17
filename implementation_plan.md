# Grasp — Agentic Institutional Brain

Build a fully-functional tool that acts as a company's institutional brain, capable of answering any question about the organization by reasoning over knowledge retrieved from five platforms: **Confluence, Jira, SharePoint, Slack, and Notion**.

---

## User Review Required

> [!IMPORTANT]
> **API Key Selection:** The system uses **Anthropic Claude** as the LLM backbone (via `anthropic` Python SDK). You will need an Anthropic API key (`ANTHROPIC_API_KEY`). The agent uses `claude-sonnet-4-20250514` for query reasoning with tool-use.

> [!IMPORTANT]
> **GitHub Repository Target:** The plan creates a local Git repository with **remote push** support. Git commits require human approval — synced content is staged as pending changes and presented for review in the dashboard before committing. After approval, changes are committed and pushed to the configured remote. Requires `GITHUB_REMOTE_URL` and `GITHUB_PAT` in `.env`.

> [!WARNING]
> **Platform Credentials:** Each of the five platforms requires API tokens/credentials. The system reads them from a `.env` file. You will need to supply actual credentials before running. The code will be fully functional — not stubbed — but it cannot run without valid tokens.

---

## Open Questions

> **Confirmed:** Claude Sonnet 4 for agentic reasoning, Claude Haiku 4 for sync summarization + content classification. Web UI included. Deployed as persistent Docker container.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         GRASP SYSTEM                             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ══════════════ SYNC LAYER (Parallel Workers) ═══════════════   │
│  ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌───────┐ ┌──────┐ │
│  │ Confluence │ │   Jira   │ │ SharePoint │ │ Slack │ │Notion│ │
│  │  Worker    │ │  Worker  │ │   Worker   │ │Worker │ │Worker│ │
│  └─────┬──────┘ └────┬─────┘ └─────┬──────┘ └───┬───┘ └──┬───┘ │
│        │             │             │            │        │      │
│        └─────────────┴──────┬──────┴────────────┴────────┘      │
│                             ▼                                    │
│               ┌──────────────────────┐                          │
│               │   Sync Orchestrator  │                          │
│               │ asyncio.gather(5)    │                          │
│               │ (Checkpointed)       │                          │
│               └──────────┬───────────┘                          │
│                          │                                       │
│              ┌───────────┴───────────┐                          │
│              ▼                       ▼                           │
│  ┌──────────────────────┐ ┌──────────────────────┐              │
│  │  Knowledge Repo (Git)│ │  ChromaDB (Vectors)  │              │
│  │  Structured Markdown │ │  Semantic Index      │              │
│  └──────────────────────┘ └──────────────────────┘              │
│                                                                  │
│  ═══════════ QUERY LAYER (Parallel Sub-Agents) ══════════════   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Coordinator Agent (Claude)                   │   │
│  │   Analyzes question → dispatches sub-agents → synthesizes │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │                                                          │   │
│  │  Phase 1: Parallel Fan-Out  (asyncio.gather)             │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │   │
│  │  │ Repo     │ │Confluence│ │  Jira    │ │SharePoint│   │   │
│  │  │ Search   │ │  Live    │ │  Live    │ │  Live    │   │   │
│  │  │ Agent    │ │  Agent   │ │  Agent   │ │  Agent   │   │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │   │
│  │  ┌──────────┐ ┌──────────┐                              │   │
│  │  │  Slack   │ │  Notion  │                              │   │
│  │  │  Live    │ │  Live    │                              │   │
│  │  │  Agent   │ │  Agent   │                              │   │
│  │  └──────────┘ └──────────┘                              │   │
│  │                                                          │   │
│  │  Phase 2: Coordinator synthesizes all results            │   │
│  │  Phase 3: Optional deep-dive (targeted follow-up)        │   │
│  └──────────────────────────────────────────────────────────┘   │
│              │                                                   │
│              ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            FastAPI + Web Dashboard                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| **LLM** | Anthropic Claude (`anthropic` SDK) | Best-in-class tool-use, 200k context, strong reasoning |
| **Vector DB** | ChromaDB (persistent, local) | Zero-infrastructure semantic search |
| **Git** | GitPython | Programmatic commits, diffing, version control |
| **Scheduler** | APScheduler (`BackgroundScheduler`) | In-process daily cron, no external deps |
| **API Server** | FastAPI + Uvicorn | Async, auto-docs, production-grade |
| **Web UI** | Vanilla HTML/CSS/JS (embedded) | Premium single-page dashboard, no build step |
| **HTTP Clients** | `httpx` (async) | Async HTTP for all platform API calls |
| **Config** | Pydantic `BaseSettings` + `.env` | Type-safe, validated configuration |

---

## Proposed Changes

### 1. Project Skeleton & Configuration

#### [NEW] [pyproject.toml](file:///c:/Users/Asmeet/Desktop/grasp/pyproject.toml)
Project metadata and dependencies:
- `anthropic`, `chromadb`, `gitpython`, `apscheduler`, `fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `pydantic-settings`, `beautifulsoup4`, `markdownify`

#### [NEW] [.env.example](file:///c:/Users/Asmeet/Desktop/grasp/.env.example)
Template for all required credentials:
- `ANTHROPIC_API_KEY`
- `GITHUB_REMOTE_URL`, `GITHUB_PAT` (for remote push)
- `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`
- `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
- `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_ID`
- `SLACK_BOT_TOKEN`
- `NOTION_API_KEY`
- `GITHUB_REPO_PATH` (local path for the knowledge repo)



#### [NEW] [src/config.py](file:///c:/Users/Asmeet/Desktop/grasp/src/config.py)
Pydantic `BaseSettings` class that loads and validates all env vars. Includes defaults and type coercion.

---

### 2. Platform Connectors

Each connector implements a common `BaseConnector` interface with:
- `async def full_retrieve() → AsyncGenerator[Document]` — streams all content with checkpointing
- `async def incremental_retrieve(since: datetime) → AsyncGenerator[Document]` — only new/changed since timestamp
- `async def live_search(query: str, hours: int = 4) → list[Document]` — real-time query for recent content
- `def checkpoint_key() → str` — unique key for checkpoint persistence

#### [NEW] [src/connectors/base.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/base.py)
- `Document` dataclass: `id`, `source`, `title`, `content`, `url`, `updated_at`, `metadata`
- `BaseConnector` abstract class with the interface above
- Shared pagination helpers, rate-limit retry logic (exponential backoff), and checkpoint save/load

#### [NEW] [src/connectors/confluence.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/confluence.py)
- Uses Confluence REST API v2 with cursor-based pagination
- Full retrieval: iterates all spaces → all pages per space, extracts body content (converts HTML → Markdown via `markdownify`)
- Incremental: uses `?sort=-modified-date` and filters by `lastModified > since`
- Live search: uses CQL `text ~ "query" AND lastModified > "now-4h"`
- Checkpoint: saves last processed space + cursor position

#### [NEW] [src/connectors/jira.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/jira.py)
- Uses Jira REST API v3 with `nextPageToken` pagination
- Full retrieval: JQL `ORDER BY created ASC`, fetches all issues with description, comments, and status
- Incremental: JQL `updated >= "since_date" ORDER BY updated ASC`
- Live search: JQL `text ~ "query" AND updated >= "-4h"`
- Checkpoint: saves last `nextPageToken`

#### [NEW] [src/connectors/sharepoint.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/sharepoint.py)
- Uses Microsoft Graph API with `ClientSecretCredential` (Azure Identity)
- Full retrieval: lists all drives → all items, downloads text-based files, lists all SharePoint lists → items with expanded fields
- Incremental: uses `delta` endpoint for drives, `$filter=lastModifiedDateTime gt 'since'` for lists
- Live search: uses Microsoft Search API `searchRequest` with `entityTypes: ["driveItem", "listItem"]`
- Checkpoint: saves delta token for each drive

#### [NEW] [src/connectors/slack.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/slack.py)
- Uses Slack Web API with cursor-based pagination
- Full retrieval: `conversations.list` → for each channel, `conversations.history` (all messages) + `conversations.replies` (threads)
- Incremental: `conversations.history` with `oldest` param set to last sync timestamp
- Live search: `search.messages` API with `query` and `sort=timestamp`
- Checkpoint: saves per-channel last processed timestamp
- Rate limiting: respects Slack's `Retry-After` headers

#### [NEW] [src/connectors/notion.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/notion.py)
- Uses Notion API with `start_cursor` pagination
- Full retrieval: `search` endpoint to discover all pages + databases → fetches full page content (blocks) and database items
- Incremental: `search` with `filter.timestamp = last_edited_time`, `start_cursor` for pagination, filters `last_edited_time > since`
- Live search: `search` API with query text
- Content extraction: recursively fetches all blocks for each page, converts rich text to Markdown
- Checkpoint: saves last cursor position

---

### 3. Sync Orchestrator & Checkpointing

#### [NEW] [src/sync/orchestrator.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/orchestrator.py)
The central sync engine that coordinates retrieval across all five connectors **in parallel**:

1. **Mode Detection:** Checks if `knowledge_repo/.grasp_state/last_sync.json` exists. If not → full sync; if yes → incremental.
2. **Parallel Worker Architecture:**
   - Each connector runs as an **independent async worker** via `asyncio.gather`
   - All 5 connectors retrieve simultaneously — total sync time ≈ slowest connector, not sum of all
   - Each worker has its own error boundary — one connector failing doesn't block the others
   - Per-worker progress tracking: `{connector: {status, docs_fetched, errors, elapsed}}`
3. **Full Sync (Checkpointed):**
   - Launches all 5 workers concurrently, each running `full_retrieve()` as an async generator
   - Each worker processes documents in batches (configurable, default 100)
   - After each batch: writes to repo filesystem, indexes in ChromaDB, saves checkpoint to `checkpoints/{connector_name}.json`
   - If interrupted, each worker resumes independently from its own checkpoint
   - On per-worker completion: deletes that worker's checkpoint, marks connector as done
   - When all workers finish: writes `last_sync.json` with timestamp
   - **Does NOT auto-commit** — changes are staged as a pending changeset for human review
4. **Incremental Sync:**
   - Reads `last_sync.json` for the `since` timestamp
   - Launches all 5 workers concurrently, each running `incremental_retrieve(since)`
   - Writes/updates files in repo filesystem, updates ChromaDB index
   - **Does NOT auto-commit** — changes are staged as a pending changeset for human review
   - Updates `last_sync.json`
5. **Pending Changeset:** After each sync, generates a changeset summary (files added/modified/deleted, per-connector counts, diff stats) and stores it in `knowledge_repo/.grasp_state/pending_changes.json`. The user reviews and approves/rejects via the dashboard or API.
6. **Error Handling:** Per-worker failures are isolated — logged with full traceback, marked as failed in status, and retried on next run. Successful workers' results are still staged.

#### [NEW] [src/sync/checkpoints.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/checkpoints.py)
- `save_checkpoint(connector: str, state: dict)` — JSON-serialized to `checkpoints/` dir
- `load_checkpoint(connector: str) → dict | None`
- `clear_checkpoint(connector: str)`
- `has_checkpoint(connector: str) → bool`

#### [NEW] [src/sync/scheduler.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/scheduler.py)
- APScheduler `BackgroundScheduler` with a `cron` trigger (default: 2:00 AM UTC daily)
- On trigger: calls `orchestrator.run_sync()`
- Includes a manual trigger endpoint for the API
- Graceful shutdown handling

---

### 4. Knowledge Repository (Git-backed)

#### [NEW] [src/repo/manager.py](file:///c:/Users/Asmeet/Desktop/grasp/src/repo/manager.py)
Manages the structured knowledge repository:

**Directory Structure (dual-organized by type + source):**

Documents are classified by **information type** using Claude Haiku during sync, then organized under type → source directories. This gives both a semantic view ("what kind of knowledge?") and a source view ("where did it come from?").

```
knowledge_repo/
├── .grasp_state/
│   ├── last_sync.json          # {"timestamp": "...", "connectors": {...}}
│   ├── sync_log.json           # History of all syncs
│   └── pending_changes.json    # Pending changeset for human review
├── architecture/               # System design, infrastructure, APIs
│   ├── confluence/
│   │   └── {page_title}.md
│   ├── notion/
│   │   └── {page_title}.md
│   └── _index.json
├── features/                   # Feature specs, PRDs, user stories
│   ├── jira/
│   │   └── {issue_key}.md
│   ├── confluence/
│   │   └── {page_title}.md
│   └── _index.json
├── operations/                 # Runbooks, SOPs, deployments, infra
│   ├── sharepoint/
│   │   └── {doc_title}.md
│   ├── confluence/
│   │   └── {page_title}.md
│   └── _index.json
├── testing/                    # Test plans, QA docs, test results
│   ├── jira/
│   │   └── {issue_key}.md
│   ├── confluence/
│   │   └── {page_title}.md
│   └── _index.json
├── decisions/                  # ADRs, meeting notes, RFCs
│   ├── confluence/
│   ├── notion/
│   └── _index.json
├── strategy/                   # Business strategy, OKRs, roadmaps
│   ├── notion/
│   ├── sharepoint/
│   └── _index.json
├── incidents/                  # Incident reports, postmortems
│   ├── jira/
│   ├── slack/
│   └── _index.json
├── discussions/                # Conversations, threads, Q&A
│   ├── slack/
│   │   └── {channel}/{date}.md
│   └── _index.json
├── references/                 # Docs, wikis, guides, onboarding
│   ├── confluence/
│   ├── sharepoint/
│   ├── notion/
│   └── _index.json
├── general/                    # Uncategorized content
│   ├── {source}/
│   └── _index.json
└── README.md                   # Auto-generated summary
```

**Information Type Taxonomy** (used by Claude Haiku classifier):
| Type | Description | Typical Sources |
|---|---|---|
| `architecture` | System design, APIs, infrastructure diagrams | Confluence, Notion |
| `features` | Feature specs, PRDs, user stories, epics | Jira, Confluence |
| `operations` | Runbooks, SOPs, deployment guides | SharePoint, Confluence |
| `testing` | Test plans, QA documentation, test results | Jira, Confluence |
| `decisions` | ADRs, meeting notes, RFCs, design reviews | Confluence, Notion |
| `strategy` | Business strategy, OKRs, roadmaps, planning | Notion, SharePoint |
| `incidents` | Incident reports, postmortems, outage logs | Jira, Slack |
| `discussions` | Conversations, threads, async Q&A | Slack |
| `references` | General docs, wikis, guides, onboarding | Confluence, SharePoint, Notion |
| `general` | Anything that doesn't fit the above | Any |

- **classify_document(doc: Document) → str:** Uses Claude Haiku to classify the document into one of the 10 information types based on title + content snippet
- **write_document(doc: Document, info_type: str):** Writes to `{info_type}/{source}/{sanitized_title}.md` with YAML frontmatter (source, URL, last updated, ID, info_type)
- **stage_pending():** Detects all unstaged changes (new/modified/deleted files), writes a summary to `pending_changes.json` with per-file diffs
- **get_pending_changes() → PendingChangeset:** Returns the current pending changeset (file list, diff stats, per-type and per-connector breakdown)
- **approve_commit(message: str | None):** Human-triggered — stages all pending files, commits via GitPython with auto-generated or user-supplied message, **pushes to remote**, clears `pending_changes.json`
- **reject_changes():** Reverts all uncommitted changes in the working tree (`git checkout -- .`), clears `pending_changes.json`
- **get_file_content(path: str) → str:** Read a specific file from repo
- **get_file_diff(path: str) → str:** Returns the `git diff` for a specific pending file
- **search_files(query: str) → list[str]:** Basic filename/path search

---

### 5. Vector Index (ChromaDB)

#### [NEW] [src/index/vector_store.py](file:///c:/Users/Asmeet/Desktop/grasp/src/index/vector_store.py)
- Persistent ChromaDB client at `./chroma_data/`
- Collection: `grasp_knowledge` with metadata fields: `source`, `title`, `url`, `updated_at`, `repo_path`
- **index_document(doc: Document):** Chunks long content (1500 chars with 200 overlap), embeds and upserts
- **search(query: str, n: int = 20, filters: dict = None) → list[SearchResult]:** Semantic search with optional source/date filters
- **delete_document(doc_id: str):** Remove stale documents
- Uses ChromaDB's built-in embedding model (all-MiniLM-L6-v2) — no API calls needed for embeddings

---

### 6. Agentic Query Engine

This is the core differentiator from basic RAG — an agent that actively reasons and pulls information. It uses a **multi-sub-agent architecture** for maximum speed.

#### [NEW] [src/agent/engine.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/engine.py)
The **Coordinator Agent** — the top-level Claude-powered agent that orchestrates the query flow:

**Three-Phase Query Architecture:**

**Phase 1 — Parallel Fan-Out (sub-agents):**
On receiving a user question, the coordinator immediately dispatches **6 sub-agents in parallel** via `asyncio.gather`:

| Sub-Agent | Runs On | Task |
|---|---|---|
| Repo Search Agent | ChromaDB | Semantic search against the full knowledge repo |
| Confluence Live Agent | Confluence API | Real-time search for content from the last 4 hours |
| Jira Live Agent | Jira API | Real-time issue/comment search from the last 4 hours |
| SharePoint Live Agent | Graph API | Real-time document/list search from the last 4 hours |
| Slack Live Agent | Slack API | Real-time message/thread search from the last 4 hours |
| Notion Live Agent | Notion API | Real-time page/database search from the last 4 hours |

All 6 run concurrently — total latency ≈ slowest sub-agent (~1-3s), not sum of all (~10-15s sequential).

Each sub-agent:
- Receives the user question + source-specific search instructions
- Executes its search (API call or vector query)
- Returns a structured result: `{source, results: [{title, snippet, url, relevance}], error?}`
- Has its own timeout (default 10s) — if one platform is slow/down, it doesn't block the others

**Phase 2 — Coordinator Synthesis:**
The coordinator agent (Claude with tool-use) receives all sub-agent results as context and:
1. Analyzes which results are most relevant to the question
2. May call `read_repo_file(path)` to get full content of highly-relevant documents
3. Synthesizes a comprehensive answer citing sources with URLs

**Phase 3 — Optional Deep-Dive:**
If the coordinator determines the answer is incomplete, it can:
- Request more specific searches via individual sub-agent tools
- Read additional repo files for deeper context
- Max 4 follow-up rounds to prevent infinite loops

**Tools available to the coordinator:**
1. `fan_out_search(query)` — Triggers Phase 1, returns all 6 sub-agent results (used automatically on first turn)
2. `read_repo_file(file_path)` — Read full content of a specific file from the Git repo
3. `search_knowledge_repo(query, source_filter, date_filter)` — Targeted semantic search against ChromaDB
4. `search_confluence_live(query)` — Targeted follow-up Confluence search
5. `search_jira_live(query)` — Targeted follow-up Jira search
6. `search_sharepoint_live(query)` — Targeted follow-up SharePoint search
7. `search_slack_live(query)` — Targeted follow-up Slack search
8. `search_notion_live(query)` — Targeted follow-up Notion search

**Agent Loop (Claude tool-use protocol):**
1. Receive user question → auto-trigger `fan_out_search` (Phase 1)
2. All 6 sub-agents execute in parallel, results aggregated
3. Send aggregated context to Claude → Claude synthesizes or requests follow-ups
4. If `stop_reason: "tool_use"` → execute requested tools (Phase 3 deep-dive)
5. If `stop_reason: "end_turn"` → return final answer with source citations
6. Max 4 follow-up rounds after initial fan-out

#### [NEW] [src/agent/sub_agents.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/sub_agents.py)
Sub-agent definitions and the parallel dispatcher:
- `SubAgent` class: wraps a search function with timeout, error boundary, and result formatting
- `SubAgentDispatcher.fan_out(query) → list[SubAgentResult]`: runs all sub-agents via `asyncio.gather(return_exceptions=True)`
- Per-agent timeout handling (returns partial results on timeout rather than failing)
- Result aggregation and deduplication across sources

#### [NEW] [src/agent/tools.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/tools.py)
Tool function definitions and their JSON schemas formatted for Claude's tool-use API (`input_schema` with JSON Schema). Includes both the `fan_out_search` meta-tool and individual platform tools for follow-up searches.

---

### 7. FastAPI Server & Web Dashboard

#### [NEW] [src/api/server.py](file:///c:/Users/Asmeet/Desktop/grasp/src/api/server.py)
FastAPI application with:
- `POST /api/query` — Submit a question, get an agentic answer (streaming SSE)
- `GET /api/status` — System status (last sync time, connector health, doc count)
- `POST /api/sync/trigger` — Manually trigger a sync
- `GET /api/sync/history` — Sync history log
- `GET /api/sources` — List all indexed sources with counts
- `GET /api/changes/pending` — Get current pending changeset (file list, diffs, stats)
- `GET /api/changes/diff/{file_path}` — Get diff for a specific pending file
- `POST /api/changes/approve` — **Human approval** — commits all pending changes
- `POST /api/changes/reject` — Revert all pending changes
- `GET /` — Serve the web dashboard
- CORS middleware, error handling, startup/shutdown events (scheduler, ChromaDB)

#### [NEW] [src/api/models.py](file:///c:/Users/Asmeet/Desktop/grasp/src/api/models.py)
Pydantic request/response models for all API endpoints.

#### [NEW] [src/static/index.html](file:///c:/Users/Asmeet/Desktop/grasp/src/static/index.html)
Premium single-page web dashboard featuring:
- **Dark mode** glassmorphism design with animated gradient background
- **Query input** with streaming response display (SSE)
- **Source citations** rendered as clickable cards linking to original platform URLs
- **System status panel** showing last sync, connector health, document counts
- **Sync control** with manual trigger button and progress indicator
- **Pending changes review panel** — shows after each sync with:
  - File list (added/modified/deleted) with per-file diff viewer
  - Per-connector breakdown (e.g., "Confluence: 12 added, 3 modified")
  - **Approve** button (commits with auto-generated message) and **Reject** button (reverts all)
  - Optional custom commit message input
  - Badge/notification when pending changes are waiting for review
- **Conversation history** sidebar with past queries
- Google Fonts (Inter), smooth transitions, micro-animations

#### [NEW] [src/static/styles.css](file:///c:/Users/Asmeet/Desktop/grasp/src/static/styles.css)
Complete CSS design system: color tokens, glassmorphism effects, animated gradients, responsive layout, scrollbar styling, loading states.

#### [NEW] [src/static/app.js](file:///c:/Users/Asmeet/Desktop/grasp/src/static/app.js)
Frontend JS: SSE handling for streaming responses, query submission, status polling, sync trigger, markdown rendering, source card rendering, history management.

---

### 8. Entry Point

#### [NEW] [main.py](file:///c:/Users/Asmeet/Desktop/grasp/main.py)
- Loads config from `.env`
- Initializes all connectors, vector store, repo manager, sync orchestrator
- Starts APScheduler
- Runs initial sync if no previous sync detected
- Launches FastAPI via Uvicorn

#### [NEW] [README.md](file:///c:/Users/Asmeet/Desktop/grasp/README.md)
- Project description, setup instructions, configuration guide, usage

---

## File Summary

| # | File | Purpose |
|---|---|---|
| 1 | `pyproject.toml` | Dependencies and project config |
| 2 | `.env.example` | Credential template |
| 3 | `.gitignore` | Git ignore rules |
| 4 | `src/config.py` | Pydantic settings |
| 5 | `src/connectors/base.py` | Base connector interface + Document model |
| 6 | `src/connectors/confluence.py` | Confluence connector |
| 7 | `src/connectors/jira.py` | Jira connector |
| 8 | `src/connectors/sharepoint.py` | SharePoint connector |
| 9 | `src/connectors/slack.py` | Slack connector |
| 10 | `src/connectors/notion.py` | Notion connector |
| 11 | `src/sync/orchestrator.py` | Sync orchestration + checkpointing |
| 12 | `src/sync/checkpoints.py` | Checkpoint persistence |
| 13 | `src/sync/scheduler.py` | APScheduler daily cron |
| 14 | `src/repo/manager.py` | Git-backed knowledge repo |
| 15 | `src/index/vector_store.py` | ChromaDB vector index |
| 16 | `src/agent/engine.py` | Coordinator agent (Claude) |
| 17 | `src/agent/sub_agents.py` | Parallel sub-agent dispatcher |
| 18 | `src/agent/tools.py` | Agent tool definitions |
| 19 | `src/api/server.py` | FastAPI routes |
| 20 | `src/api/models.py` | Pydantic API models |
| 21 | `src/static/index.html` | Web dashboard |
| 22 | `src/static/styles.css` | Dashboard CSS |
| 23 | `src/static/app.js` | Dashboard JS |
| 24 | `main.py` | Entry point |
| 25 | `Dockerfile` | Docker image |
| 26 | `docker-compose.yml` | Docker compose config |
| 27 | `README.md` | Documentation |

---

## Verification Plan

### Automated Tests
```bash
# Install dependencies
pip install -e ".[dev]"

# Start the server (requires .env with valid credentials)
python main.py

# Verify API is running
curl http://localhost:8000/api/status
```

### Manual Verification
1. **Configuration Validation:** Start the server with missing credentials → should produce clear error messages
2. **Full Sync:** Run with valid credentials → observe checkpointed retrieval across all platforms, files appearing in `knowledge_repo/`
3. **Incremental Sync:** Wait or trigger a second sync → only new/changed content is fetched
4. **Query Test:** Submit questions via the web UI → agent uses both repo search and live queries, returns cited answers
5. **Checkpoint Recovery:** Kill the process mid-sync → restart → sync resumes from checkpoint
6. **Web UI:** Open `http://localhost:8000` → verify premium design, streaming responses, source cards
