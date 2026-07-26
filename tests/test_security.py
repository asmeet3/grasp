from __future__ import annotations

import pytest

from src.core.security import AuthContext, Permission, PolicyEngine, SystemRole


def context(role: str = "member", organization: str = "acme") -> AuthContext:
    return AuthContext.from_user(
        {"id": "user-1", "organization_id": organization, "system_role": role}
    )


def test_roles_are_security_roles_not_job_titles() -> None:
    member = AuthContext.from_user(
        {
            "id": "user-1",
            "organization_id": "acme",
            "job_title": "Partner",
            "system_role": "member",
        }
    )
    assert Permission.QUERY in member.permissions
    assert Permission.REVIEW not in member.permissions


def test_document_acl_is_default_deny() -> None:
    policy = PolicyEngine()
    member = context()
    assert not policy.can_access_document(member, {})
    assert policy.can_access_document(member, {"acl_principals": ["organization:acme"]})
    assert not policy.can_access_document(member, {"acl_principals": ["organization:other"]})


def test_reviewer_can_review_but_member_cannot() -> None:
    policy = PolicyEngine()
    with pytest.raises(PermissionError):
        policy.require(context(), Permission.REVIEW)
    reviewer = context(SystemRole.REVIEWER.value)
    assert policy.require(reviewer, Permission.REVIEW) is reviewer


def test_contributions_use_stable_user_identity() -> None:
    policy = PolicyEngine()
    member = context()
    assert policy.can_access_contribution(member, "user-1")
    assert not policy.can_access_contribution(member, "same display name")
    reviewer = context(SystemRole.REVIEWER.value)
    assert policy.can_access_contribution(reviewer, "someone-else")
