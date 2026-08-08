"""Core contracts shared by Grasp's API, workers, and domain services."""

from .changes import (
    ChangeSetConflict,
    ChangeSetState,
    require_current_base,
    require_transition,
    summarize_operations,
)
from .interfaces import (
    AuditStore,
    ChangeSetStore,
    ContextProvider,
    JobQueue,
    KnowledgeRepository,
    SearchIndex,
)
from .security import AuthContext, Permission, PolicyEngine, SystemRole

__all__ = [
    "AuditStore",
    "AuthContext",
    "ChangeSetStore",
    "ChangeSetConflict",
    "ChangeSetState",
    "ContextProvider",
    "JobQueue",
    "KnowledgeRepository",
    "Permission",
    "PolicyEngine",
    "SearchIndex",
    "SystemRole",
    "require_current_base",
    "require_transition",
    "summarize_operations",
]
