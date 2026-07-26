from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from src.connectors.base import Document
from src.ingestion import IngestionCandidate


def test_legacy_document_adapter_adds_hash_and_explicit_org_acl() -> None:
    document = Document(
        id="external-123",
        source="notion",
        title="Current policy",
        content="Only committed content is searchable.",
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    candidate = IngestionCandidate.from_document(document, organization_id="acme")
    assert candidate.external_id == "external-123"
    assert candidate.content_hash == hashlib.sha256(document.content.encode()).hexdigest()
    assert candidate.acl_principals == ("organization:acme",)
    assert candidate.to_document().metadata["content_hash"] == candidate.content_hash


def test_source_acl_is_preserved() -> None:
    document = Document(
        id="restricted",
        source="sharepoint",
        title="Board notes",
        content="Restricted",
        metadata={"acl_principals": ["user:director", "team:board"]},
    )
    candidate = IngestionCandidate.from_document(document, organization_id="acme")
    assert candidate.acl_principals == ("user:director", "team:board")
