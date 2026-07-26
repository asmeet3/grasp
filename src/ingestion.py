"""Immutable connector-neutral ingestion records and compatibility adapters."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .connectors.base import Document


@dataclass(frozen=True, slots=True)
class IngestionCandidate:
    source: str
    external_id: str
    title: str
    content: str
    content_hash: str
    source_revision: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    acl_principals: tuple[str, ...] = ()
    domain: str = "general"
    sensitivity: str = "internal"
    provenance_url: str = ""
    deleted: bool = False
    connector_cursor: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_document(
        cls,
        document: Document,
        *,
        organization_id: str = "default",
    ) -> IngestionCandidate:
        metadata = dict(document.metadata or {})
        acl = metadata.get("acl_principals") or (f"organization:{organization_id}",)
        if isinstance(acl, str):
            acl = tuple(part.strip() for part in acl.split(",") if part.strip())
        else:
            acl = tuple(str(part) for part in acl if part)
        digest = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        return cls(
            source=document.source,
            external_id=document.id,
            title=document.title,
            content=document.content,
            content_hash=digest,
            source_revision=str(metadata.get("source_revision") or metadata.get("etag") or ""),
            created_at=metadata.get("created_at") or document.updated_at,
            updated_at=document.updated_at,
            acl_principals=acl,
            domain=str(metadata.get("domain") or "general"),
            sensitivity=str(metadata.get("sensitivity") or "internal"),
            provenance_url=document.url,
            deleted=bool(metadata.get("deleted", False)),
            connector_cursor=str(metadata.get("connector_cursor") or ""),
            metadata=metadata,
        )

    def to_document(self) -> Document:
        metadata = dict(self.metadata)
        metadata.update(
            {
                "acl_principals": list(self.acl_principals),
                "domain": self.domain,
                "sensitivity": self.sensitivity,
                "content_hash": self.content_hash,
                "source_revision": self.source_revision,
                "deleted": self.deleted,
            }
        )
        return Document(
            id=self.external_id,
            source=self.source,
            title=self.title,
            content=self.content,
            url=self.provenance_url,
            updated_at=self.updated_at,
            metadata=metadata,
        )
