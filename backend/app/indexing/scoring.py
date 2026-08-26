"""Portable deterministic scoring used by SQLite tests and local development.

The lexical fallback is TF-IDF cosine similarity. It is explicitly *not BM25*.
PostgreSQL uses `ts_rank_cd` when its FTS adapter is selected; that score is also
not described as BM25. The common result contract allows a later true-BM25 engine.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from backend.app.providers.fake import tokenize

from .schemas import RetrievalResult, SearchFilters


@dataclass(frozen=True, slots=True)
class SearchRecord:
    chunk_id: str
    document_id: str
    corpus_version_id: str
    text: str
    metadata: Mapping[str, Any]
    embedding: Sequence[float] | None = None


def _matches(record: SearchRecord, filters: SearchFilters | None) -> bool:
    if filters is None:
        return True
    if filters.document_ids and record.document_id not in filters.document_ids:
        return False
    return all(record.metadata.get(key) == value for key, value in filters.metadata_equals.items())


def portable_lexical_search(
    query: str,
    records: Iterable[SearchRecord],
    *,
    top_k: int,
    filters: SearchFilters | None = None,
) -> list[RetrievalResult]:
    """Rank with corpus-level TF-IDF cosine; zero-score records are omitted."""

    if top_k < 1:
        raise ValueError("top_k must be positive")
    selected = [record for record in records if _matches(record, filters)]
    if not selected:
        return []
    query_counts = Counter(tokenize(query))
    if not query_counts:
        return []
    doc_counts = [Counter(tokenize(record.text)) for record in selected]
    document_frequency: Counter[str] = Counter()
    for counts in doc_counts:
        document_frequency.update(counts.keys())
    total = len(selected)

    def idf(term: str) -> float:
        return math.log((total + 1) / (document_frequency[term] + 1)) + 1.0

    query_vector = {
        term: (1.0 + math.log(count)) * idf(term) for term, count in query_counts.items()
    }
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    scored: list[tuple[float, SearchRecord]] = []
    for record, counts in zip(selected, doc_counts, strict=True):
        doc_vector = {
            term: (1.0 + math.log(count)) * idf(term) for term, count in counts.items()
        }
        doc_norm = math.sqrt(sum(value * value for value in doc_vector.values()))
        dot = sum(query_vector[term] * doc_vector.get(term, 0.0) for term in query_vector)
        score = dot / (query_norm * doc_norm) if query_norm and doc_norm else 0.0
        if score > 0.0:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return [
        RetrievalResult(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            corpus_version_id=record.corpus_version_id,
            text=record.text,
            rank=rank,
            score=score,
            retrieval_method="lexical_tfidf_cosine",
            metadata=record.metadata,
        )
        for rank, (score, record) in enumerate(scored[:top_k], start=1)
    ]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def portable_dense_search(
    query_embedding: Sequence[float],
    records: Iterable[SearchRecord],
    *,
    top_k: int,
    filters: SearchFilters | None = None,
) -> list[RetrievalResult]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    scored = [
        (cosine_similarity(query_embedding, record.embedding), record)
        for record in records
        if record.embedding is not None and _matches(record, filters)
    ]
    scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return [
        RetrievalResult(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            corpus_version_id=record.corpus_version_id,
            text=record.text,
            rank=rank,
            score=score,
            retrieval_method="dense_cosine",
            metadata=record.metadata,
        )
        for rank, (score, record) in enumerate(scored[:top_k], start=1)
    ]
