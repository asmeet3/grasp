from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.database import create_engine, init_db, observability_snapshots_table
from src.observability import MetricRecorder, MetricSessionStore

ROOT = Path(__file__).resolve().parents[1]


class SyncOrchestratorStub:
    is_running = False
    worker_statuses = {}

    async def get_last_sync(self):
        return None

    async def get_sync_history(self):
        return []

    async def run_sync(self):
        return {}


class SchedulerStub:
    next_run_time = None


class RepositoryStub:
    def get_source_stats(self):
        return {}


class IndexStub:
    def get_stats(self):
        return {}


class UserManagerStub:
    def __init__(self):
        self.users = [
            {
                "id": "member-user",
                "organization_id": "default",
                "system_role": "member",
                "status": "approved",
                "role": "Associate",
                "job_title": "Associate",
            },
            {
                "id": "admin-user",
                "organization_id": "default",
                "system_role": "administrator",
                "status": "approved",
                "role": "Partner",
                "job_title": "Partner",
            },
        ]

    async def verify_token(self, token: str):
        user_id = {"member-token": "member-user", "admin-token": "admin-user"}.get(token)
        return next((dict(user) for user in self.users if user["id"] == user_id), None)

    async def list_users(self, organization_id: str | None = None):
        return [
            dict(user)
            for user in self.users
            if organization_id is None or user["organization_id"] == organization_id
        ]

    async def count_administrators(self, organization_id: str | None = None):
        return sum(
            user["status"] == "approved"
            and user["system_role"] == "administrator"
            and (organization_id is None or user["organization_id"] == organization_id)
            for user in self.users
        )


class AuditStoreStub:
    def __init__(self):
        self.events = [
            {
                "id": 1,
                "event_type": "agent.run.completed",
                "actor_user_id": "member-user",
                "organization_id": "default",
                "resource_type": "agent",
                "resource_id": "agent-1",
                "details": {"ok": True},
                "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            },
            {
                "id": 2,
                "event_type": "changeset.approved",
                "actor_user_id": "admin-user",
                "organization_id": "default",
                "resource_type": "change_set",
                "resource_id": "cs-1",
                "details": {},
                "created_at": datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            },
        ]
        self.summary_data = {
            "total": 2,
            "days": 30,
            "since": "2026-07-09T12:00:00+00:00",
            "by_event_type": {"agent.run.completed": 1, "changeset.approved": 1},
            "by_day": [
                {"date": "2026-08-01", "count": 1},
                {"date": "2026-08-02", "count": 1},
            ],
        }
        self.last_query: dict | None = None
        self.last_summary: dict | None = None

    async def query(self, **kwargs):
        self.last_query = kwargs
        return list(self.events), len(self.events)

    async def summary(self, **kwargs):
        self.last_summary = kwargs
        return dict(self.summary_data)


class ObservabilityStoreStub:
    def __init__(self):
        self.sessions = [
            {
                "id": "snap-1",
                "session_id": "session-1",
                "started_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                "captured_at": datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                "host": "test-host",
                "metrics": {
                    "agent.run_duration_seconds": {
                        "count": 3,
                        "p50": 1.5,
                        "p95": 2.5,
                        "maximum": 2.5,
                    }
                },
            }
        ]

    async def list_sessions(self, limit: int = 20):
        return list(self.sessions)


def client(audit_store=None, metrics=None, observability_store=None) -> TestClient:
    app = create_app(
        query_engine=object(),
        sync_orchestrator=SyncOrchestratorStub(),
        sync_scheduler=SchedulerStub(),
        repo_manager=RepositoryStub(),
        vector_store=IndexStub(),
        connectors={},
        contribution_manager=object(),
        user_manager=UserManagerStub(),
        admin_key="bootstrap-secret",
        audit=audit_store,
        metrics=metrics,
        observability_store=observability_store,
    )
    return TestClient(app)


def test_audit_events_endpoint_requires_view_audit_permission() -> None:
    api = client(AuditStoreStub())
    assert api.get("/api/admin/audit-events").status_code == 401
    assert (
        api.get(
            "/api/admin/audit-events",
            headers={"Authorization": "Bearer member-token"},
        ).status_code
        == 403
    )
    response = api.get(
        "/api/admin/audit-events",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["events"][0]["event_type"] == "agent.run.completed"


def test_audit_events_query_passes_filters_and_scopes_organization() -> None:
    store = AuditStoreStub()
    api = client(store)
    response = api.get(
        "/api/admin/audit-events",
        headers={"Authorization": "Bearer admin-token"},
        params={
            "event_type": "agent.run.completed",
            "actor": "member-user",
            "resource_type": "agent",
            "resource_id": "agent-",
            "start": "2026-08-01T00:00:00",
            "end": "2026-08-01T23:59:59",
            "limit": 10,
            "offset": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 10
    assert body["offset"] == 5
    assert store.last_query is not None
    assert store.last_query["organization_id"] == "default"
    assert store.last_query["event_type"] == "agent.run.completed"
    assert store.last_query["actor_user_id"] == "member-user"
    assert store.last_query["resource_type"] == "agent"
    assert store.last_query["resource_id"] == "agent-"
    assert store.last_query["limit"] == 10
    assert store.last_query["offset"] == 5
    assert store.last_query["start"].isoformat() == "2026-08-01T00:00:00"
    assert store.last_query["end"].isoformat() == "2026-08-01T23:59:59"


def test_audit_events_reject_invalid_datetimes() -> None:
    api = client(AuditStoreStub())
    response = api.get(
        "/api/admin/audit-events",
        headers={"Authorization": "Bearer admin-token"},
        params={"start": "not-a-date"},
    )
    assert response.status_code == 422


def test_audit_events_summary_endpoint() -> None:
    store = AuditStoreStub()
    api = client(store)
    response = api.get(
        "/api/admin/audit-events/summary",
        headers={"Authorization": "Bearer admin-token"},
        params={"days": 14},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["by_event_type"]["changeset.approved"] == 1
    assert store.last_summary == {"organization_id": "default", "days": 14}


def test_observability_endpoint_returns_metric_snapshot() -> None:
    recorder = MetricRecorder()
    recorder.observe("agent.run_duration_seconds", 1.5)
    recorder.observe("agent.run_duration_seconds", 2.5)
    api = client(audit_store=AuditStoreStub(), metrics=recorder)

    assert api.get("/api/admin/observability").status_code == 401
    response = api.get(
        "/api/admin/observability",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metric_count"] == 1
    metric = body["metrics"]["agent.run_duration_seconds"]
    assert metric["count"] == 2
    assert metric["p50"] == 1.5
    assert metric["p95"] == 2.5
    assert body["captured_at"]
    assert body["sessions"] == []


def test_observability_endpoint_returns_session_history() -> None:
    store = ObservabilityStoreStub()
    api = client(audit_store=AuditStoreStub(), observability_store=store)

    response = api.get(
        "/api/admin/observability",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["sessions"]) == 1
    session = body["sessions"][0]
    assert session["session_id"] == "session-1"
    assert session["started_at"] == "2026-08-01T12:00:00+00:00"
    assert session["captured_at"] == "2026-08-02T12:00:00+00:00"
    assert session["host"] == "test-host"
    assert session["metrics"]["agent.run_duration_seconds"]["count"] == 3


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="requires a PostgreSQL database",
)
async def test_metric_session_store_roundtrip() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        await init_db(engine)
        # Remove any rows left by a prior failed run before inserting fresh data.
        async with engine.begin() as conn:
            await conn.execute(
                observability_snapshots_table.delete().where(
                    observability_snapshots_table.c.session_id == "store-test-session"
                )
            )
        store = MetricSessionStore(
            engine,
            session_id="store-test-session",
            started_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            host="store-test-host",
        )
        await store.save_snapshot(
            {
                "agent.run_duration_seconds": {
                    "count": 3,
                    "p50": 1.5,
                    "p95": 2.5,
                    "maximum": 2.5,
                }
            }
        )
        await store.save_snapshot({})
        sessions = await store.list_sessions(limit=10)
        assert sessions
        latest = sessions[0]
        assert latest["session_id"] == "store-test-session"
        assert latest["host"] == "store-test-host"
        assert latest["metrics"]["agent.run_duration_seconds"]["count"] == 3
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                observability_snapshots_table.delete().where(
                    observability_snapshots_table.c.session_id == "store-test-session"
                )
            )
        await engine.dispose()


def test_operations_page_and_sidebar_ui_present() -> None:
    api = client(AuditStoreStub())
    response = api.get("/admin/operations")
    assert response.status_code == 200
    assert 'id="navAudit"' in response.text
    assert 'id="screenAudit"' in response.text
    assert 'id="navObservability"' in response.text
    assert 'id="screenObservability"' in response.text
    assert "Audit Log" in response.text
    assert "Observability" in response.text

    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    assert "screenName === 'Audit'" in javascript
    assert "screenName === 'Observability'" in javascript
    assert "'/admin/operations'" in javascript
    assert "loadAuditEvents" in javascript
    assert "loadAuditSummary" in javascript
    assert "loadObservability" in javascript
