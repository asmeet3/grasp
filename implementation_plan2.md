# Grasp — Full Audit Report

Complete review of the built system against the original project scope and implementation plan.

---

## Scope Coverage Summary

| Original Requirement | Status | Notes |
|---|---|---|
| Multi-source retrieval (5 platforms) | ✅ Complete | All 5 connectors implemented |
| Checkpointed initial sync | ✅ Complete | Full sync with per-connector checkpoints and resume |
| Incremental daily updates | ✅ Complete | APScheduler cron + `incremental_retrieve()` |
| Repository structuring (Git-backed) | ✅ Complete | 10-type taxonomy, YAML frontmatter, per-source dirs |
| Real-time query augmentation | ✅ Complete | 6 parallel sub-agents at query time |
| Agentic AI (not basic RAG) | ✅ Complete | Claude tool-use with multi-round follow-ups |
| Human-approved Git commits | ✅ Complete | Pending changeset → approve/reject flow |
| Web dashboard | ✅ Complete | Glassmorphism dark-mode UI with SSE streaming |
| Docker deployment | ⚠️ Has a bug | Dockerfile build order issue (see #1) |
| SharePoint file content retrieval | ❌ Missing | Only captures file metadata, not actual content |

**Verdict:** All major scope items are structurally present and the integration wiring is correct. However, there are **4 critical bugs** that will cause failures at runtime, **1 scope gap** where functionality is incomplete, and **4 infrastructure items** needed for the system to actually work.

---

## Critical Bugs (Will Cause Runtime Failures)

### 1. Blocking Anthropic API calls destroy async parallelism

> [!CAUTION]
> This is the most impactful bug. It completely negates the parallel sync and concurrent query architectures.

**Where:** [manager.py](file:///c:/Users/Asmeet/Desktop/grasp/src/repo/manager.py#L71) and [engine.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/engine.py#L67)

**Problem:** Both files instantiate `anthropic.Anthropic()` — the **synchronous** client — and call it from `async` code paths:

- `RepoManager.classify_document()` (line 143) calls `self.anthropic_client.messages.create()` synchronously. This is called from `classify_and_write()` → `SyncOrchestrator._process_document()`, which runs inside `asyncio.gather()` parallel workers. **Every classification call blocks the entire event loop**, meaning all 5 connector workers pause whenever any one of them classifies a document. The parallel sync architecture effectively becomes sequential.

- `QueryEngine.query_stream()` (line 126) calls `self.client.messages.create()` synchronously. During Claude reasoning, the entire FastAPI server is blocked — no other requests (status polls, other queries, sync triggers) can be processed.

**Fix:** Replace `anthropic.Anthropic` with `anthropic.AsyncAnthropic` and `await` the `.messages.create()` calls. This requires:
- `engine.py`: Change to `AsyncAnthropic`, use `await self.client.messages.create(...)`
- `manager.py`: Change to `AsyncAnthropic`, make `classify_document()` async, use `await`

---

### 2. `reject_changes()` destroys sync state

> [!CAUTION]
> After rejecting pending changes, the system loses all memory of previous syncs and will re-run a full historical sync.

**Where:** [manager.py](file:///c:/Users/Asmeet/Desktop/grasp/src/repo/manager.py#L415-L436)

**Problem:** `reject_changes()` runs `git clean -fd` at line 425. This removes **all untracked files** from the knowledge repo directory. The `.grasp_state/` directory lives inside the repo and contains:
- `last_sync.json` — tracks when the last sync happened (determines full vs. incremental)
- `sync_log.json` — complete sync history

These state files are written directly to disk during sync but are **never committed** to Git (they're internal state). This means they are **untracked files**. Running `git clean -fd` deletes them.

**Impact:** After any "reject", the system thinks no sync has ever occurred → triggers a full historical re-sync of everything.

**Fix:** Two changes needed:
1. Add a `.gitignore` file **inside the `knowledge_repo/` directory** that ignores `.grasp_state/`
2. Modify `reject_changes()` to exclude `.grasp_state/` from the clean operation, e.g. `git clean -fd --exclude=.grasp_state/`

---

### 3. Dockerfile build will fail

**Where:** [Dockerfile](file:///c:/Users/Asmeet/Desktop/grasp/Dockerfile#L13-L14)

**Problem:** The Dockerfile copies only `pyproject.toml` (line 13), then immediately runs `pip install -e .` (line 14). An editable install (`-e`) requires the package source code to be present in the working directory. Since `COPY . .` doesn't happen until line 17, there's no `src/` directory when pip runs, causing a build failure.

**Fix:** Either:
- Change `pip install -e .` to `pip install .` (non-editable), **and** move `COPY . .` before the pip install, OR
- Split into two stages: first copy only `pyproject.toml` and install deps with `pip install --no-cache-dir .` (which will fail if no source), then copy everything. The cleanest fix is: `COPY . .` first, then `pip install --no-cache-dir .` (non-editable).

---

### 4. SSE streaming drops newlines in Claude responses

**Where:** [app.js](file:///c:/Users/Asmeet/Desktop/grasp/src/static/app.js#L286-L311)

**Problem:** The SSE parser in the frontend concatenates `data:` lines without inserting newlines between them:

```javascript
if (line.startsWith('data: ')) {
    const data = line.slice(6);
    if (data === '') continue;
    fullText += data;  // ← no newline inserted
}
```

The `sse_starlette` library splits multi-line data into separate `data:` lines per SSE spec. For example, a Claude chunk containing `"Hello\nWorld"` becomes:
```
data: Hello
data: World
```

The client parses this as `fullText = "HelloWorld"` — the newline is lost. This corrupts the formatting of every multi-line response, making code blocks, lists, and paragraphs run together.

**Fix:** Track when consecutive `data:` lines belong to the same event and insert `\n` between them. Or switch to using `EventSource` API on the client side, which handles multi-line data natively.

---

## Scope Gap (Incomplete Against Original Requirements)

### 5. SharePoint connector does not download file content

**Where:** [sharepoint.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/sharepoint.py#L310-L357) — `_drive_item_to_document()`

**Original scope (implementation plan line 154):**
> *"downloads text-based files"*

**What it actually does:** Captures only file **metadata** (name, path, drive, size, description). The `content` field is just:
```
**File:** report.docx
**Path:** Documents/Q4/report.docx
**Drive:** Documents
**Size:** 45,230 bytes
```

The actual file content is never downloaded from SharePoint. This means SharePoint documents cannot be meaningfully searched or answered from — only their filenames and locations are indexed.

**Fix:** After identifying a text-based file, download its content via the Graph API's `/content` endpoint for plain-text files, or use the `/preview` endpoint. For Office documents (.docx, .xlsx, .pptx), use the conversion endpoint that returns HTML/text. Add content extraction to `_drive_item_to_document()`.

---

## Significant Integration Issues (Degraded Functionality)

### 6. Vector store leaves stale chunks on document update

**Where:** [vector_store.py](file:///c:/Users/Asmeet/Desktop/grasp/src/index/vector_store.py#L55-L93) — `index_document()`

**Problem:** When a document is updated during incremental sync, `index_document()` uses `upsert()` which correctly updates existing chunk IDs. But if the updated document is **shorter** than before (fewer chunks), the old excess chunks remain in ChromaDB as ghost entries.

Example: Document initially has 5 chunks (`doc-1::chunk-0` through `chunk-4`). After update, it has 3 chunks. Chunks 0-2 are upserted, but chunks 3-4 remain in the index with stale content. These stale chunks will appear in search results.

**Fix:** Before upserting, delete all existing chunks for the document ID: call `self.delete_document(doc.id)` before the `upsert()` call.

---

### 7. Unnecessary dependencies in `pyproject.toml`

**Where:** [pyproject.toml](file:///c:/Users/Asmeet/Desktop/grasp/pyproject.toml#L23-L27)

**Problem:** Four SDK packages are listed as dependencies but **never imported** anywhere in the codebase:
- `azure-identity>=1.17.0` — SharePoint uses direct `httpx` OAuth2 calls
- `msgraph-sdk>=1.5.0` — SharePoint uses direct Graph API calls via `httpx`
- `slack-sdk>=3.30.0` — Slack uses direct Web API calls via `httpx`
- `notion-client>=2.2.0` — Notion uses direct API calls via `httpx`

All five connectors use `httpx` directly for all API communication. These unused dependencies add ~100MB+ to the install, increase build time, and could cause dependency conflicts.

**Fix:** Remove all four lines from `[project.dependencies]`.

---

### 8. Confluence connector makes redundant API call per space

**Where:** [confluence.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/confluence.py#L107-L119) — `_get_space_pages()`

**Problem:** `_get_space_pages(space_key)` receives the space key, then makes an extra API call to look up the space by key to get its `id`. But the caller (`full_retrieve`) already has the full space dict from `_get_all_spaces()` which contains the `id` field. This doubles the API calls during full sync.

**Fix:** Pass `space_id` directly to `_get_space_pages()` instead of `space_key`, sourcing it from the `space["id"]` dict in `full_retrieve()`.

---

## Missing Infrastructure (Not in Scope But Necessary)

### 9. No `.gitignore` inside the knowledge repo

**Related to bug #2.** The `knowledge_repo/` directory is a Git repository, but it has no `.gitignore` file to protect internal state. The `.grasp_state/` directory (containing `last_sync.json`, `sync_log.json`, `pending_changes.json`) should be gitignored because:
- These are internal state files, not knowledge content
- They should not be committed to the knowledge repo
- They must survive `git clean` operations (bug #2)

**Fix:** When `RepoManager._init_repo()` creates the initial commit, include a `.gitignore` containing `.grasp_state/`.

---

### 10. No error handling for missing `ANTHROPIC_API_KEY` at connector-level

**Where:** [config.py](file:///c:/Users/Asmeet/Desktop/grasp/src/config.py#L24)

**Problem:** `ANTHROPIC_API_KEY` is defined with `Field(...)` (required, no default). If it's missing, Pydantic raises a `ValidationError` at startup, which is caught in `main.py`. This part is fine.

However, the system can start with **zero connectors** configured (line 92-93 of `main.py` just logs a warning). In this state, the query engine is operational but has nothing to search — every fan-out returns empty results. The system would answer "no results found" to everything without any obvious indication to the user of why.

**Fix:** Add a startup warning in the dashboard when no connectors are configured, or display the connector status more prominently on the welcome screen.

---

### 11. No `httpx` client cleanup in connectors on error paths

**Where:** All 5 connectors create `httpx.AsyncClient` instances via `_get_client()` that persist for the lifetime of the connector. These are closed in the `close()` method, which is called from `main.py`'s shutdown handler. However, if the server crashes or is killed (e.g., mid-sync), these clients are never closed, potentially leaking connections.

This is a minor issue for typical usage but worth noting for robustness.

---

### 12. Health check endpoint calls all connectors sequentially-ish

**Where:** [server.py](file:///c:/Users/Asmeet/Desktop/grasp/src/api/server.py#L86-L103) — `get_status()`

**Problem:** The status endpoint runs `connector.health_check()` with `asyncio.wait_for(timeout=5.0)` in a **sequential loop** (line 91). If all 5 connectors are configured and any are slow/down, the status endpoint can take up to 25 seconds. The dashboard polls this every 30 seconds, so the dashboard can appear frozen.

**Fix:** Run all health checks in parallel via `asyncio.gather()` instead of a sequential loop.

---

## Integration Flow Verification

I traced the complete data flow through the system. Here is the path and my finding for each:

### Sync Flow: ✅ Correctly Integrated (with bugs noted above)

```
Scheduler/Manual trigger
  → SyncOrchestrator.run_sync()
    → needs_full_sync() checks last_sync.json
    → _full_sync() or _incremental_sync()
      → asyncio.gather(5 workers)
        → connector.full_retrieve(checkpoint) yields list[Document]
        → _process_document(doc):
            → repo_manager.classify_and_write(doc) → writes .md file to disk
            → vector_store.index_document(doc) → upserts chunks in ChromaDB
        → checkpoints.save_checkpoint() after each batch
      → checkpoints.clear_checkpoint() on completion
    → _save_sync_state() writes last_sync.json + sync_log.json
    → repo_manager.stage_pending() detects changes, writes pending_changes.json
```

Integration is correct. Bugs #1 and #2 affect runtime behavior but the wiring is sound.

### Query Flow: ✅ Correctly Integrated

```
User submits question via dashboard
  → POST /api/query → EventSourceResponse
    → QueryEngine.query_stream(question)
      → Phase 1: tool_executor.execute("fan_out_search")
        → SubAgentDispatcher.fan_out(query)
          → asyncio.gather(6 sub-agents)
            → repo_search: VectorStore.search() → Document list
            → 5x connector.live_search() → Document list each
          → format_all_results() → context string
      → Phase 2: Claude messages.create() with context + tools
        → Claude synthesizes or requests follow-up tools
      → Phase 3: If tool_use → execute tools → continue loop (max 4 rounds)
      → Yield text chunks via async generator
  → SSE events streamed to dashboard
  → app.js parses SSE, renders Markdown
```

Integration is correct. Bug #4 affects display quality.

### Approval Flow: ✅ Correctly Integrated

```
Dashboard shows pending badge → user clicks → modal
  → GET /api/changes/pending → returns changeset summary
  → User clicks Approve:
    → POST /api/changes/approve with optional message
      → repo_manager.approve_commit()
        → git add -A → git commit → git push to remote
        → clears pending_changes.json
  → User clicks Reject:
    → POST /api/changes/reject
      → repo_manager.reject_changes()
        → git checkout -- . → git clean -fd ← BUG #2
        → clears pending_changes.json
```

Integration is correct. Bug #2 affects reject behavior.

---

## Files That Are Working Correctly (Leave As-Is)

| File | Status |
|---|---|
| [config.py](file:///c:/Users/Asmeet/Desktop/grasp/src/config.py) | ✅ Correct — Pydantic settings, connector detection, path derivation |
| [base.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/base.py) | ✅ Correct — Document model, RateLimiter, BaseConnector ABC, HTML→MD |
| [jira.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/jira.py) | ✅ Correct — JQL, ADF parsing, nextPageToken pagination |
| [slack.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/slack.py) | ✅ Correct — Cursor pagination, thread replies, per-day grouping |
| [notion.py](file:///c:/Users/Asmeet/Desktop/grasp/src/connectors/notion.py) | ✅ Correct — Block recursion, rich text extraction, search pagination |
| [checkpoints.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/checkpoints.py) | ✅ Correct — JSON file persistence, save/load/clear/has |
| [orchestrator.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/orchestrator.py) | ✅ Correct — Parallel workers, error boundaries, state management |
| [scheduler.py](file:///c:/Users/Asmeet/Desktop/grasp/src/sync/scheduler.py) | ✅ Correct — APScheduler cron, thread-safe async dispatch |
| [sub_agents.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/sub_agents.py) | ✅ Correct — Timeout, error boundary, result formatting, fan-out |
| [tools.py](file:///c:/Users/Asmeet/Desktop/grasp/src/agent/tools.py) | ✅ Correct — Tool schemas, executor routing, graceful missing-connector handling |
| [models.py](file:///c:/Users/Asmeet/Desktop/grasp/src/api/models.py) | ✅ Correct — Pydantic request/response models |
| [styles.css](file:///c:/Users/Asmeet/Desktop/grasp/src/static/styles.css) | ✅ Correct — Design system, glassmorphism, animations, responsive |
| [index.html](file:///c:/Users/Asmeet/Desktop/grasp/src/static/index.html) | ✅ Correct — Sidebar, chat, modal, semantic structure |
| [.env.example](file:///c:/Users/Asmeet/Desktop/grasp/.env.example) | ✅ Correct — All variables documented |
| [docker-compose.yml](file:///c:/Users/Asmeet/Desktop/grasp/docker-compose.yml) | ✅ Correct — Volume persistence, health check, env file |
| [README.md](file:///c:/Users/Asmeet/Desktop/grasp/README.md) | ✅ Correct — Architecture, setup, config table, API docs |
| All `__init__.py` files | ✅ Correct — Present with comments |

---

## Proposed Fix Priority

| # | Issue | Severity | Effort |
|---|---|---|---|
| 1 | Sync → async Anthropic client | **Critical** | Medium — change 2 files |
| 2 | `reject_changes()` destroys sync state | **Critical** | Small — add gitignore + exclude flag |
| 3 | Dockerfile build order | **Critical** | Small — reorder 2 lines |
| 4 | SSE newline drops | **Critical** | Small — fix JS parser |
| 5 | SharePoint file content download | **Scope gap** | Medium — add content download to connector |
| 6 | Stale vector chunks on update | **Significant** | Small — add delete before upsert |
| 7 | Remove unused dependencies | **Significant** | Small — delete 4 lines from pyproject.toml |
| 8 | Confluence redundant space lookup | **Minor** | Small — pass space_id instead of key |
| 9 | Knowledge repo .gitignore | **Infrastructure** | Small — add file in _init_repo |
| 10 | Zero-connector startup UX | **Infrastructure** | Small — add dashboard warning |
| 11 | httpx client cleanup | **Infrastructure** | Minor — already handled on graceful shutdown |
| 12 | Sequential health checks | **Infrastructure** | Small — use asyncio.gather |

## Verification Plan

After applying fixes:

```bash
# 1. Verify Docker builds
docker build -t grasp .

# 2. Verify server starts with only ANTHROPIC_API_KEY
echo "ANTHROPIC_API_KEY=test" > .env
python main.py  # Should start, show 0 connectors warning

# 3. Verify reject doesn't destroy state
# Run sync, then reject, then check .grasp_state/ still exists

# 4. Verify API is responsive during query
# Submit a query, simultaneously poll /api/status — should not hang
```
