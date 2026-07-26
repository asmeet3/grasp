from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.server import create_app


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
    def __init__(self, include_administrator: bool = False):
        self.updated_role: tuple[str, str, str | None] | None = None
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
                "id": "target-user",
                "organization_id": "default",
                "system_role": "member",
                "status": "approved",
                "role": "Manager",
                "job_title": "Manager",
            },
            {
                "id": "pending-user",
                "organization_id": "default",
                "system_role": "member",
                "status": "pending_approval",
                "role": None,
                "job_title": None,
            },
            {
                "id": "other-org-user",
                "organization_id": "other",
                "system_role": "member",
                "status": "approved",
                "role": "Associate",
                "job_title": "Associate",
            },
        ]
        if include_administrator:
            self.users.append(
                {
                    "id": "admin-user",
                    "organization_id": "default",
                    "system_role": "administrator",
                    "status": "approved",
                    "role": "Partner",
                    "job_title": "Partner",
                }
            )

    async def verify_token(self, token: str):
        user_id = {
            "member-token": "member-user",
            "target-token": "target-user",
            "admin-token": "admin-user",
        }.get(token)
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

    async def update_role(
        self,
        user_id: str,
        role: str,
        system_role: str | None = None,
        *,
        actor_user_id: str | None = None,
    ):
        self.updated_role = (user_id, role, system_role)
        user = next(user for user in self.users if user["id"] == user_id)
        user["role"] = role
        user["job_title"] = role
        if system_role is not None:
            user["system_role"] = system_role
        return {
            "user": dict(user),
        }

    async def approve_user(
        self,
        user_id: str,
        role: str,
        system_role: str,
        *,
        actor_user_id: str | None = None,
    ):
        user = next(user for user in self.users if user["id"] == user_id)
        user.update(status="approved", role=role, job_title=role, system_role=system_role)
        return {"user": dict(user)}

    async def reject_user(self, user_id: str, *, actor_user_id: str | None = None):
        user = next(user for user in self.users if user["id"] == user_id)
        user["status"] = "rejected"
        return {"user": dict(user)}


def client(
    admin_key: str = "",
    user_manager: UserManagerStub | None = None,
    agent_service=None,
) -> TestClient:
    app = create_app(
        query_engine=object(),
        sync_orchestrator=SyncOrchestratorStub(),
        sync_scheduler=SchedulerStub(),
        repo_manager=RepositoryStub(),
        vector_store=IndexStub(),
        connectors={},
        contribution_manager=object(),
        user_manager=user_manager or UserManagerStub(),
        admin_key=admin_key,
        agent_service=agent_service,
    )
    return TestClient(app)


def test_company_content_endpoints_require_authentication() -> None:
    api = client()
    assert api.post("/api/query", json={"question": "secret?"}).status_code == 401
    assert api.get("/api/sources").status_code == 401
    assert (
        api.post(
            "/api/contributions/submit",
            json={"title": "x", "content": "y", "content_type": "plain_text"},
        ).status_code
        == 401
    )


def test_member_cannot_enumerate_review_queue() -> None:
    response = client().get(
        "/api/changes/pending",
        headers={"Authorization": "Bearer member-token"},
    )
    assert response.status_code == 403


def test_liveness_is_the_only_public_status_endpoint() -> None:
    api = client()
    assert api.get("/api/health/live").status_code == 200
    assert api.get("/api/status").status_code == 401


def test_bootstrap_key_can_open_user_administration_without_a_session() -> None:
    api = client(admin_key="bootstrap-secret")

    assert api.get("/api/admin/access").status_code == 403
    access = api.get(
        "/api/admin/access",
        headers={"X-Admin-Key": "bootstrap-secret"},
    )
    users = api.get(
        "/api/admin/users",
        headers={"X-Admin-Key": "bootstrap-secret"},
    )

    assert access.status_code == 200
    assert access.json() == {"authenticated": True, "bootstrap": True}
    assert users.status_code == 200
    assert users.json()["count"] == 4


def test_bootstrap_status_distinguishes_first_run_from_an_initialized_system() -> None:
    first_run = client(admin_key="bootstrap-secret").get("/api/admin/bootstrap/status")
    not_configured = client().get("/api/admin/bootstrap/status")
    initialized = client(
        admin_key="bootstrap-secret",
        user_manager=UserManagerStub(include_administrator=True),
    ).get("/api/admin/bootstrap/status")

    assert first_run.json() == {
        "bootstrap_required": True,
        "bootstrap_configured": True,
    }
    assert not_configured.json() == {
        "bootstrap_required": True,
        "bootstrap_configured": False,
    }
    assert initialized.json() == {
        "bootstrap_required": False,
        "bootstrap_configured": True,
    }


def test_signed_in_user_can_claim_administrator_with_bootstrap_key() -> None:
    api = client(admin_key="bootstrap-secret")

    without_session = api.post(
        "/api/admin/bootstrap/claim",
        headers={"X-Admin-Key": "bootstrap-secret"},
    )
    claim = api.post(
        "/api/admin/bootstrap/claim",
        headers={
            "Authorization": "Bearer member-token",
            "X-Admin-Key": "bootstrap-secret",
        },
    )

    assert without_session.status_code == 401
    assert claim.status_code == 200
    assert claim.json()["user"]["system_role"] == "administrator"


def test_bootstrap_can_only_approve_the_first_account_as_administrator() -> None:
    manager = UserManagerStub()
    api = client(admin_key="bootstrap-secret", user_manager=manager)
    headers = {"X-Admin-Key": "bootstrap-secret"}

    member_approval = api.post(
        "/api/admin/users/pending-user/approve",
        headers=headers,
        json={"role": "Associate", "system_role": "member"},
    )
    administrator_approval = api.post(
        "/api/admin/users/pending-user/approve",
        headers=headers,
        json={"role": "Associate", "system_role": "administrator"},
    )
    reused_key = api.get("/api/admin/access", headers=headers)

    assert member_approval.status_code == 403
    assert administrator_approval.status_code == 200
    assert administrator_approval.json()["user"]["system_role"] == "administrator"
    assert reused_key.status_code == 403


def test_bootstrap_key_stops_working_after_an_administrator_exists() -> None:
    api = client(
        admin_key="bootstrap-secret",
        user_manager=UserManagerStub(include_administrator=True),
    )

    response = api.get(
        "/api/admin/access",
        headers={"X-Admin-Key": "bootstrap-secret"},
    )

    assert response.status_code == 403


def test_member_cannot_manage_users_even_with_bootstrap_key() -> None:
    manager = UserManagerStub(include_administrator=True)
    api = client(admin_key="bootstrap-secret", user_manager=manager)

    list_response = api.get(
        "/api/admin/users",
        headers={
            "Authorization": "Bearer member-token",
            "X-Admin-Key": "bootstrap-secret",
        },
    )
    update_response = api.put(
        "/api/admin/users/target-user/role",
        headers={
            "Authorization": "Bearer member-token",
            "X-Admin-Key": "bootstrap-secret",
        },
        json={"role": "Manager", "system_role": "operator"},
    )

    assert list_response.status_code == 403
    assert update_response.status_code == 403
    assert manager.updated_role is None


def test_administrator_can_change_another_users_access() -> None:
    manager = UserManagerStub(include_administrator=True)
    api = client(user_manager=manager)

    response = api.put(
        "/api/admin/users/target-user/role",
        headers={"Authorization": "Bearer admin-token"},
        json={"role": "Manager", "system_role": "operator"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["system_role"] == "operator"
    assert manager.updated_role == ("target-user", "Manager", "operator")


def test_administrator_cannot_change_own_access_or_cross_organization_boundary() -> None:
    manager = UserManagerStub(include_administrator=True)
    api = client(user_manager=manager)

    self_change = api.put(
        "/api/admin/users/admin-user/role",
        headers={"Authorization": "Bearer admin-token"},
        json={"role": "Partner", "system_role": "member"},
    )
    cross_org_change = api.put(
        "/api/admin/users/other-org-user/role",
        headers={"Authorization": "Bearer admin-token"},
        json={"role": "Associate", "system_role": "operator"},
    )

    assert self_change.status_code == 409
    assert cross_org_change.status_code == 404
    assert manager.updated_role is None


def test_operator_can_enter_agent_operations_but_cannot_manage_users() -> None:
    class AgentServiceStub:
        async def control_status(self, _context):
            return {"enabled": True, "emergency_stopped": False, "reason": ""}

    manager = UserManagerStub(include_administrator=True)
    target = next(user for user in manager.users if user["id"] == "target-user")
    target["system_role"] = "operator"
    api = client(user_manager=manager, agent_service=AgentServiceStub())
    headers = {"Authorization": "Bearer target-token"}

    assert api.get("/api/admin/access", headers=headers).status_code == 200
    assert api.get("/api/agents/status", headers=headers).status_code == 200
    assert api.get("/api/admin/users", headers=headers).status_code == 403
    assert (
        api.get(
            "/api/agents/status",
            headers={"Authorization": "Bearer member-token"},
        ).status_code
        == 403
    )
