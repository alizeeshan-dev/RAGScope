"""Typed contracts for fixed, version-isolated retrieval."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from backend.app.db.models import RetrievalMode
from backend.app.indexing.schemas import RetrievalResult, SearchFilters


@dataclass(frozen=True, slots=True)
class MetadataFilters:
    """Allow-listed retrieval filters; values can never become SQL fragments."""

    document_ids: frozenset[UUID] = field(default_factory=frozenset)
    publication_years: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if any(not isinstance(value, UUID) for value in self.document_ids):
            raise ValueError("document_ids must contain UUID values")
        if any(
            isinstance(year, bool)
            or not isinstance(year, int)
            or year < 1000
            or year > 9999
            for year in self.publication_years
        ):
            raise ValueError("publication years must be four-digit positive years")


@dataclass(frozen=True, slots=True)
class RetrieverConfiguration:
    candidate_count: int = 20

    def __post_init__(self) -> None:
        if self.candidate_count < 1 or self.candidate_count > 1000:
            raise ValueError("candidate_count must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class RRFConfiguration:
    rank_constant: int = 60
    lexical_weight: float = 1.0
    dense_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.rank_constant < 1:
            raise ValueError("RRF rank_constant must be positive")
        if not math.isfinite(self.lexical_weight) or not math.isfinite(self.dense_weight):
            raise ValueError("RRF weights must be finite")
        if self.lexical_weight < 0 or self.dense_weight < 0:
            raise ValueError("RRF weights cannot be negative")
        if self.lexical_weight == self.dense_weight == 0:
            raise ValueError("at least one RRF weight must be positive")


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    corpus_version_id: UUID
    query: str
    mode: RetrievalMode
    top_k: int = 10
    filters: MetadataFilters = field(default_factory=MetadataFilters)
    lexical: RetrieverConfiguration = field(default_factory=RetrieverConfiguration)
    dense: RetrieverConfiguration = field(default_factory=RetrieverConfiguration)
    fusion: RRFConfiguration = field(default_factory=RRFConfiguration)

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("retrieval query cannot be empty")
        if self.top_k < 1 or self.top_k > 1000:
            raise ValueError("top_k must be between 1 and 1000")


@dataclass(frozen=True, slots=True)
class RetrievedCandidate:
    chunk_id: UUID
    document_id: UUID
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    page_start: int | None = None
    page_end: int | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    lexical_normalized_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    dense_normalized_score: float | None = None
    fused_rank: int | None = None
    fusion_score: float | None = None
    rrf_contributions: Mapping[str, float] = field(default_factory=dict)

    @property
    def rank(self) -> int:
        rank = self.fused_rank or self.lexical_rank or self.dense_rank
        if rank is None:
            raise ValueError("candidate has no stage rank")
        return rank

    @property
    def score(self) -> float:
        """Score for the latest retrieval stage (fusion before raw engines)."""

        score = self.fusion_score
        if score is None:
            score = self.lexical_score if self.lexical_score is not None else self.dense_score
        if score is None:
            raise ValueError("candidate has no stage score")
        return score


@dataclass(frozen=True, slots=True)
class RetrievalStageRecord:
    chunk_id: UUID
    retriever_type: str
    original_rank: int | None = None
    original_score: float | None = None
    normalized_score: float | None = None
    fused_rank: int | None = None
    fusion_score: float | None = None
    timing_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalExecution:
    mode: RetrievalMode
    candidates: tuple[RetrievedCandidate, ...] = ()
    records: tuple[RetrievalStageRecord, ...] = ()
    retrieval_disabled: bool = False
    total_timing_ms: int = 0
    lexical_timing_ms: int | None = None
    dense_timing_ms: int | None = None
    fusion_timing_ms: int | None = None


@runtime_checkable
class Retriever(Protocol):
    """Common adapter boundary over any lexical or dense implementation."""

    retriever_type: str

    def retrieve(
        self,
        corpus_version_id: UUID,
        query: str,
        *,
        candidate_count: int,
        filters: SearchFilters | None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Sequence[RetrievalResult]: ...
