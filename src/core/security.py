"""Authentication context and default-deny authorization policy."""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class Permission(str, enum.Enum):
    QUERY = "query"
    CONTRIBUTE = "contribute"
    REVIEW = "review"
    MANAGE_USERS = "manage_users"
    MANAGE_AGENTS = "manage_agents"
    VIEW_AUDIT = "view_audit"


class SystemRole(str, enum.Enum):
    MEMBER = "member"
    KNOWLEDGE_EDITOR = "knowledge_editor"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"


ROLE_PERMISSIONS: dict[SystemRole, frozenset[Permission]] = {
    SystemRole.MEMBER: frozenset({Permission.QUERY, Permission.CONTRIBUTE}),
    SystemRole.KNOWLEDGE_EDITOR: frozenset(
        {Permission.QUERY, Permission.CONTRIBUTE, Permission.REVIEW}
    ),
    SystemRole.OPERATOR: frozenset(
        {
            Permission.QUERY,
            Permission.CONTRIBUTE,
            Permission.REVIEW,
            Permission.MANAGE_AGENTS,
            Permission.VIEW_AUDIT,
        }
    ),
    SystemRole.ADMINISTRATOR: frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Authenticated request identity propagated through every operation."""

    user_id: str
    organization_id: str
    system_role: SystemRole
    permissions: frozenset[Permission]
    principals: frozenset[str]
    allowed_domains: frozenset[str] = frozenset()
    allowed_classifications: frozenset[str] = frozenset()

    @classmethod
    def from_user(cls, user: Mapping[str, Any]) -> AuthContext:
        role_value = user.get("system_role") or SystemRole.MEMBER.value
        try:
            role = SystemRole(role_value)
        except ValueError:
            role = SystemRole.MEMBER
        organization_id = str(user.get("organization_id") or "default")
        user_id = str(user["id"])
        principals = {
            f"user:{user_id}",
            f"organization:{organization_id}",
            f"role:{role.value}",
        }
        for principal in user.get("principals") or ():
            if isinstance(principal, str) and principal:
                principals.add(principal)
        return cls(
            user_id=user_id,
            organization_id=organization_id,
            system_role=role,
            permissions=ROLE_PERMISSIONS[role],
            principals=frozenset(principals),
        )


class PolicyEngine:
    """Central default-deny policy service.

    ACLs are evaluated before content is returned to a model.  A document with
    no explicit principals is deliberately inaccessible; ingestion assigns an
    organization principal when importing legacy content.
    """

    def require(self, context: AuthContext | None, permission: Permission) -> AuthContext:
        if context is None or permission not in context.permissions:
            raise PermissionError(f"Permission required: {permission.value}")
        return context

    def allows(self, context: AuthContext | None, permission: Permission) -> bool:
        return bool(context and permission in context.permissions)

    def can_access_principals(
        self,
        context: AuthContext,
        acl_principals: Iterable[str] | None,
    ) -> bool:
        acl = frozenset(p for p in (acl_principals or ()) if p)
        return bool(acl and context.principals.intersection(acl))

    def can_access_document(self, context: AuthContext, metadata: Mapping[str, Any]) -> bool:
        acl = metadata.get("acl_principals")
        if isinstance(acl, str):
            acl = [part.strip() for part in acl.split(",") if part.strip()]
        if not self.can_access_principals(context, acl):
            return False
        domain = str(metadata.get("domain") or "general").strip().lower()
        classification = (
            str(metadata.get("sensitivity") or metadata.get("classification") or "internal")
            .strip()
            .lower()
        )
        if context.allowed_domains and domain not in context.allowed_domains:
            return False
        return not (
            context.allowed_classifications
            and classification not in context.allowed_classifications
        )

    def can_access_contribution(
        self,
        context: AuthContext,
        submitter_user_id: str | None,
    ) -> bool:
        return submitter_user_id == context.user_id or Permission.REVIEW in context.permissions
