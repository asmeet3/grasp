from __future__ import annotations

from dataclasses import replace

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


def test_knowledge_editor_can_review_but_member_cannot() -> None:
    policy = PolicyEngine()
    with pytest.raises(PermissionError):
        policy.require(context(), Permission.REVIEW)
    editor = context(SystemRole.KNOWLEDGE_EDITOR.value)
    assert policy.require(editor, Permission.REVIEW) is editor


def test_contributions_use_stable_user_identity() -> None:
    policy = PolicyEngine()
    member = context()
    assert policy.can_access_contribution(member, "user-1")
    assert not policy.can_access_contribution(member, "same display name")
    editor = context(SystemRole.KNOWLEDGE_EDITOR.value)
    assert policy.can_access_contribution(editor, "someone-else")


def test_agent_scope_narrows_acl_authorized_documents() -> None:
    policy = PolicyEngine()
    scoped = replace(
        context(SystemRole.OPERATOR.value),
        allowed_domains=frozenset({"engineering"}),
        allowed_classifications=frozenset({"internal"}),
    )
    acl = ["organization:acme"]
    assert policy.can_access_document(
        scoped,
        {"acl_principals": acl, "domain": "engineering", "sensitivity": "internal"},
    )
    assert not policy.can_access_document(
        scoped,
        {"acl_principals": acl, "domain": "finance", "sensitivity": "internal"},
    )
    assert not policy.can_access_document(
        scoped,
        {"acl_principals": acl, "domain": "engineering", "sensitivity": "restricted"},
    )
