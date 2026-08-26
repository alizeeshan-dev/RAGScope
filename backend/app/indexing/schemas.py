from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Validated, intentionally small Chunk 1 metadata-filter boundary."""

    document_ids: frozenset[str] = field(default_factory=frozenset)
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunk_id: str
    document_id: str
    corpus_version_id: str
    text: str
    rank: int
    score: float
    retrieval_method: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IndexIntegrityReport:
    index_id: str
    corpus_version_id: str
    valid: bool
    expected_chunk_count: int
    indexed_chunk_count: int
    failure_count: int
    foreign_chunk_count: int
    missing_chunk_count: int
    invalid_entry_count: int
    errors: tuple[str, ...] = ()
