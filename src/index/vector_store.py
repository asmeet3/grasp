"""Vector store — ChromaDB-based semantic search index.

Provides document indexing with recursive markdown-aware chunking,
semantic search via OpenAI text-embedding-3-large, metadata filtering,
and persistent local storage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

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

    def __init__(
        self,
        persist_dir: Path,
        openai_api_key: str = "",
        embedding_model: str = "text-embedding-3-large",
    ):
        self.persist_dir = persist_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))

        # Use OpenAI embedding function if an API key is provided,
        # otherwise fall back to ChromaDB's default (all-MiniLM-L6-v2).
        self._embedding_fn = None
        if openai_api_key:
            self._embedding_fn = OpenAIEmbeddingFunction(
                api_key=openai_api_key,
                model_name=embedding_model,
            )
            logger.info(f"Using OpenAI embedding model: {embedding_model}")
        else:
            logger.warning(
                "No OPENAI_API_KEY provided — falling back to ChromaDB default "
                "embedding model (all-MiniLM-L6-v2). Set OPENAI_API_KEY for "
                "text-embedding-3-large."
            )

        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=self._embedding_fn,
        )
        logger.info(
            f"VectorStore initialized at {self.persist_dir} "
            f"({self._collection.count()} documents)"
        )

    # Indexing

    def index_document(self, doc: Document, info_type: str = "topics"):
        """Index a document, chunking if necessary."""
        from ..connectors.base import sanitize_filename

        date_prefix = doc.updated_at.strftime("%Y")
        slug = sanitize_filename(doc.title)
        repo_path = doc.metadata.get(
            "repo_path", f"knowledge/{info_type}/{date_prefix}-{slug}.md"
        )

        chunks = self._chunk_text(doc.content)
        if not chunks:
            return

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

    # Markdown-aware chunking

    # Separator hierarchy — try the most structural separators first,
    # fall back to progressively finer-grained ones.
    _SEPARATORS = [
        # 1. Markdown headers (section boundaries)
        re.compile(r"(?=^#{1,6} )", re.MULTILINE),
        # 2. Code block fences (never split inside a code block)
        re.compile(r"(?<=\n)```\n|```\n", re.MULTILINE),
        # 3. Horizontal rules
        re.compile(r"\n(?:---+|\*\*\*+|___+)\n"),
        # 4. Double newlines (paragraph breaks)
        re.compile(r"\n\n+"),
        # 5. Single newlines (line breaks)
        re.compile(r"\n"),
        # 6. Sentence endings
        re.compile(r"(?<=[.!?])\s+"),
        # 7. Word boundaries (spaces)
        re.compile(r" "),
    ]

    def _chunk_text(self, text: str) -> list[str]:
        """Recursively split text into overlapping chunks.

        Uses a hierarchy of separators — markdown headers first, then
        code blocks, paragraphs, sentences, and finally words — to
        produce semantically coherent chunks that respect document
        structure.
        """
        if not text or not text.strip():
            return []

        if len(text) <= self.CHUNK_SIZE:
            return [text]

        # Recursively split, then apply overlap
        raw_chunks = self._recursive_split(text, separator_index=0)

        # Apply overlap between adjacent chunks
        return self._apply_overlap(raw_chunks)

    def _recursive_split(self, text: str, separator_index: int) -> list[str]:
        """Split text using the separator at the given index.

        If the resulting pieces are still too large, recurse with the
        next separator level. If we run out of separators, hard-split
        by character count.
        """
        # Base case: text fits in a single chunk
        if len(text) <= self.CHUNK_SIZE:
            stripped = text.strip()
            return [stripped] if stripped else []

        # If we've exhausted all separators, hard-split by character count
        if separator_index >= len(self._SEPARATORS):
            return self._hard_split(text)

        separator = self._SEPARATORS[separator_index]
        pieces = separator.split(text)

        # If this separator didn't split at all (or only produced 1 piece),
        # try the next separator level
        if len(pieces) <= 1:
            return self._recursive_split(text, separator_index + 1)

        # Merge small pieces back together up to CHUNK_SIZE, then recurse
        # any piece that's still too large with the next separator
        chunks: list[str] = []
        current_buffer = ""

        for piece in pieces:
            # If adding this piece would exceed the limit, flush the buffer
            if current_buffer and len(current_buffer) + len(piece) > self.CHUNK_SIZE:
                stripped = current_buffer.strip()
                if stripped:
                    chunks.append(stripped)
                current_buffer = ""

            current_buffer += piece

            # If the buffer itself exceeds the limit even with just this
            # piece, it needs recursive splitting at a finer level
            if len(current_buffer) > self.CHUNK_SIZE:
                sub_chunks = self._recursive_split(
                    current_buffer, separator_index + 1
                )
                chunks.extend(sub_chunks)
                current_buffer = ""

        # Flush remaining buffer
        stripped = current_buffer.strip()
        if stripped:
            # If it's still too large, recurse
            if len(stripped) > self.CHUNK_SIZE:
                chunks.extend(
                    self._recursive_split(stripped, separator_index + 1)
                )
            else:
                chunks.append(stripped)

        return chunks

    def _hard_split(self, text: str) -> list[str]:
        """Last resort — split by raw character count."""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.CHUNK_SIZE, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
        return chunks

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """Add overlapping context between adjacent chunks.

        Takes the last CHUNK_OVERLAP characters of the previous chunk
        and prepends them to the current chunk.
        """
        if len(chunks) <= 1:
            return chunks

        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            # Take the tail of the previous chunk as overlap context
            overlap_text = prev[-self.CHUNK_OVERLAP:] if len(prev) > self.CHUNK_OVERLAP else prev
            # Find a clean break point in the overlap (word boundary)
            space_idx = overlap_text.find(" ")
            if space_idx > 0:
                overlap_text = overlap_text[space_idx + 1:]

            combined = overlap_text + "\n" + chunks[i]
            # Truncate if the overlap made it too long
            if len(combined) > self.CHUNK_SIZE + self.CHUNK_OVERLAP:
                combined = combined[:self.CHUNK_SIZE + self.CHUNK_OVERLAP]
            overlapped.append(combined)

        return overlapped

    # Search

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
                for i in range(len(results["ids"][0])):
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    content = results["documents"][0][i] if results["documents"] else ""

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

    # Management

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
