"""Vector store — ChromaDB-based semantic search index.

Provides document indexing with chunking, semantic search with
metadata filtering, and persistent local storage.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import chromadb

from ..connectors.base import Document

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result from the vector store."""
    doc_id: str
    title: str
    content: str
    source: str
    url: str
    info_type: str
    repo_path: str
    score: float
    updated_at: str


class VectorStore:
    """ChromaDB-backed vector store for semantic search over the knowledge repo."""

    COLLECTION_NAME = "grasp_knowledge"
    CHUNK_SIZE = 1500
    CHUNK_OVERLAP = 200

    def __init__(self, persist_dir: Path):
        self.persist_dir = persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"VectorStore initialized at {self.persist_dir} ({self._collection.count()} documents)")

    # ── Indexing ───────────────────────────────────────────

    def index_document(self, doc: Document, info_type: str = "topics"):
        """Index a document, chunking if necessary."""
        from ..connectors.base import sanitize_filename

        date_prefix = doc.updated_at.strftime("%Y")
        slug = sanitize_filename(doc.title)
        repo_path = f"knowledge/{info_type}/{date_prefix}-{slug}.md"

        chunks = self._chunk_text(doc.content)
        if not chunks:
            return

        # Remove any existing chunks for this document to avoid stale data
        # (e.g., if the document shrunk and now has fewer chunks)
        self.delete_document(doc.id)

        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc.id}::chunk-{i}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "doc_id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "url": doc.url,
                "info_type": info_type,
                "repo_path": repo_path,
                "updated_at": doc.updated_at.isoformat(),
                "chunk_index": i,
                "total_chunks": len(chunks),
            })

        try:
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as e:
            logger.error(f"Failed to index document {doc.id}: {e}")

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks."""
        if not text or not text.strip():
            return []

        if len(text) <= self.CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self.CHUNK_SIZE

            # Try to break at a paragraph or sentence boundary
            if end < len(text):
                # Look for paragraph break
                para_break = text.rfind("\n\n", start + self.CHUNK_SIZE // 2, end)
                if para_break > start:
                    end = para_break + 2
                else:
                    # Look for sentence break
                    for sep in [". ", ".\n", "! ", "? "]:
                        sent_break = text.rfind(sep, start + self.CHUNK_SIZE // 2, end)
                        if sent_break > start:
                            end = sent_break + len(sep)
                            break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - self.CHUNK_OVERLAP if end < len(text) else end

        return chunks

    # ── Search ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        n_results: int = 20,
        source_filter: str | None = None,
        info_type_filter: str | None = None,
    ) -> list[SearchResult]:
        """Semantic search with optional metadata filters."""
        where_filters = {}
        if source_filter:
            where_filters["source"] = source_filter
        if info_type_filter:
            where_filters["info_type"] = info_type_filter

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filters if where_filters else None,
            )

            search_results = []
            if results and results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    content = results["documents"][0][i] if results["documents"] else ""

                    # Convert distance to similarity score (cosine: 1 - distance)
                    score = max(0.0, 1.0 - distance)

                    search_results.append(SearchResult(
                        doc_id=metadata.get("doc_id", ""),
                        title=metadata.get("title", ""),
                        content=content,
                        source=metadata.get("source", ""),
                        url=metadata.get("url", ""),
                        info_type=metadata.get("info_type", ""),
                        repo_path=metadata.get("repo_path", ""),
                        score=score,
                        updated_at=metadata.get("updated_at", ""),
                    ))

            # Deduplicate by doc_id, keeping highest score
            seen: dict[str, SearchResult] = {}
            for sr in search_results:
                if sr.doc_id not in seen or sr.score > seen[sr.doc_id].score:
                    seen[sr.doc_id] = sr

            return sorted(seen.values(), key=lambda x: x.score, reverse=True)

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    # ── Management ─────────────────────────────────────────

    def delete_document(self, doc_id: str):
        """Remove all chunks for a document."""
        try:
            # Find all chunk IDs for this document
            results = self._collection.get(
                where={"doc_id": doc_id},
                include=[],
            )
            if results and results["ids"]:
                self._collection.delete(ids=results["ids"])
                logger.debug(f"Deleted {len(results['ids'])} chunks for {doc_id}")
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")

    @property
    def document_count(self) -> int:
        """Total number of indexed chunks."""
        return self._collection.count()

    def get_stats(self) -> dict:
        """Get index statistics."""
        return {
            "total_chunks": self._collection.count(),
            "persist_dir": str(self.persist_dir),
        }
