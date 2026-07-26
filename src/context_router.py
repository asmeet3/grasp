"""Token-budgeted canonical company and domain context selection."""

from __future__ import annotations

from dataclasses import dataclass

from .core.security import AuthContext, PolicyEngine
from .repo.manager import RepoManager

DOMAIN_TERMS = {
    "product": ("product", "roadmap", "feature"),
    "sales": ("sales", "deal", "pipeline", "prospect"),
    "marketing": ("marketing", "campaign", "brand"),
    "customer-success": ("customer success", "renewal", "support"),
    "finance": ("finance", "budget", "revenue", "invoice"),
    "hr": ("hiring", "employee", "hr", "people"),
    "legal": ("legal", "contract", "terms"),
    "compliance": ("compliance", "audit", "control"),
}


@dataclass(frozen=True, slots=True)
class RoutedContext:
    intent: str
    domains: tuple[str, ...]
    text: str
    estimated_tokens: int


class ContextRouter:
    def __init__(
        self,
        repository: RepoManager,
        *,
        token_budget: int = 8000,
        policy: PolicyEngine | None = None,
    ):
        self.repository = repository
        self.token_budget = token_budget
        self.policy = policy or PolicyEngine()

    def route(self, query: str, context: AuthContext) -> RoutedContext:
        lowered = query.lower()
        domains = tuple(
            domain
            for domain, terms in DOMAIN_TERMS.items()
            if any(term in lowered for term in terms)
        ) or ("general",)
        intent = (
            "action"
            if any(term in lowered for term in ("create", "send", "update"))
            else "question"
        )
        paths = ["company/CONTEXT.md"]
        paths.extend(f"domains/{domain}/CONTEXT.md" for domain in domains if domain != "general")
        remaining_chars = self.token_budget * 4
        parts: list[str] = []
        for path in paths:
            metadata = self.repository.get_committed_file_metadata(path)
            if not self.policy.can_access_document(context, metadata):
                continue
            content = self.repository.read_committed_file(path)
            if not content:
                continue
            excerpt = content[:remaining_chars]
            parts.append(f"--- {path} ---\n{excerpt}")
            remaining_chars -= len(excerpt)
            if remaining_chars <= 0:
                break
        text = "\n\n".join(parts)
        return RoutedContext(intent, domains, text, (len(text) + 3) // 4)
