# Grasp — Agentic Institutional Brain

An AI-powered tool that acts as your company's institutional brain, capable of answering any question about the organization by reasoning over knowledge from **Confluence, Jira, SharePoint, Slack, and Notion**.

Unlike basic RAG systems that only search what's been indexed, Grasp is an **agentic AI** — it actively pulls live information from all sources at query time and synthesizes comprehensive answers with source citations.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    GRASP SYSTEM                       │
├──────────────────────────────────────────────────────┤
│                                                      │
│  SYNC LAYER — 5 parallel workers                    │
│  Confluence │ Jira │ SharePoint │ Slack │ Notion    │
│       ↓ asyncio.gather(5) ↓                         │
│  Sync Orchestrator → Git Repo + ChromaDB            │
│                                                      │
│  QUERY LAYER — 6 parallel sub-agents                │
│  ┌─────────────────────────────────────────────┐    │
│  │  Coordinator Agent (Claude Sonnet 4)        │    │
│  │  Phase 1: Fan-out → 6 sub-agents parallel   │    │
│  │  Phase 2: Synthesize answer                 │    │
│  │  Phase 3: Optional deep-dive follow-ups     │    │
│  └─────────────────────────────────────────────┘    │
│       ↓                                              │
│  FastAPI + Web Dashboard                             │
│                                                      │
└──────────────────────────────────────────────────────┘
```

## Key Features

- **Multi-platform retrieval**: Confluence, Jira, SharePoint, Slack, Notion
- **Parallel sub-agents**: Query-time fan-out to all sources simultaneously (~1-3s total)
- **Checkpointed sync**: Full initial sync with resume capability; daily incremental updates
- **Type-classified storage**: Documents auto-classified into 10 categories (architecture, features, operations, etc.)
- **Human-approved commits**: Changes staged for review; commit + push only on approval
- **Git-backed knowledge repo**: Version-controlled, structured Markdown with YAML frontmatter
- **Live + cached queries**: Combines indexed repo with real-time platform searches
- **Premium web dashboard**: Dark glassmorphism UI with streaming responses

## Quick Start

### 1. Configure credentials

```bash
cp .env.example .env
# Edit .env with your API keys and platform credentials
```

### 2. Run with Docker (recommended)

```bash
docker-compose up -d
```

### 3. Run locally

```bash
pip install -e .
python main.py
```

### 4. Open the dashboard

Navigate to [http://localhost:8000](http://localhost:8000)

## Configuration

All configuration is via environment variables (`.env` file):

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key for Claude |
| `GITHUB_REMOTE_URL` | ✅ | Git remote URL for the knowledge repo |
| `GITHUB_PAT` | ✅ | GitHub Personal Access Token for push |
| `CONFLUENCE_URL` | ◻️ | Confluence base URL |
| `CONFLUENCE_EMAIL` | ◻️ | Confluence account email |
| `CONFLUENCE_API_TOKEN` | ◻️ | Confluence API token |
| `JIRA_URL` | ◻️ | Jira base URL |
| `JIRA_EMAIL` | ◻️ | Jira account email |
| `JIRA_API_TOKEN` | ◻️ | Jira API token |
| `SHAREPOINT_TENANT_ID` | ◻️ | Azure tenant ID |
| `SHAREPOINT_CLIENT_ID` | ◻️ | Azure app client ID |
| `SHAREPOINT_CLIENT_SECRET` | ◻️ | Azure app client secret |
| `SHAREPOINT_SITE_ID` | ◻️ | SharePoint site ID |
| `SLACK_BOT_TOKEN` | ◻️ | Slack bot token |
| `NOTION_API_KEY` | ◻️ | Notion integration key |
| `SYNC_CRON_HOUR` | ◻️ | Daily sync hour UTC (default: 2) |
| `SYNC_CRON_MINUTE` | ◻️ | Daily sync minute (default: 0) |

Only `ANTHROPIC_API_KEY` is strictly required. Connectors are auto-detected — configure only the platforms you use.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/query` | Submit a question (SSE streaming response) |
| `GET` | `/api/status` | System status, connector health, doc counts |
| `POST` | `/api/sync/trigger` | Manually trigger a sync |
| `GET` | `/api/sync/status` | Current sync progress |
| `GET` | `/api/sync/history` | Sync history log |
| `GET` | `/api/changes/pending` | Pending changeset for review |
| `POST` | `/api/changes/approve` | Approve and commit changes |
| `POST` | `/api/changes/reject` | Reject and revert changes |
| `GET` | `/api/sources` | Document counts by source/type |

## Repository Structure

Documents are organized by **information type** → **source platform**:

```
knowledge_repo/
├── architecture/    # System design, APIs, infrastructure
├── features/        # Feature specs, PRDs, user stories
├── operations/      # Runbooks, SOPs, deployments
├── testing/         # Test plans, QA docs
├── decisions/       # ADRs, meeting notes, RFCs
├── strategy/        # Business strategy, OKRs, roadmaps
├── incidents/       # Incident reports, postmortems
├── discussions/     # Conversations, threads (Slack)
├── references/      # General docs, wikis, guides
└── general/         # Uncategorized
```

Each document is a Markdown file with YAML frontmatter containing source, URL, timestamps, and classification metadata.

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Anthropic Claude (Sonnet 4 + Haiku 4) |
| Vector DB | ChromaDB (local, persistent) |
| Git | GitPython |
| Scheduler | APScheduler |
| API | FastAPI + Uvicorn |
| Web UI | Vanilla HTML/CSS/JS |
| Deployment | Docker |
