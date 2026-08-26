"""Optional reranking that preserves every earlier retrieval-stage value."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import RetrievalResultRecord
from backend.app.providers.base import RerankCandidate, RerankerProvider


class RerankableCandidate(Protocol):
    """Structural boundary implemented by retrieval-layer candidates."""

    @property
    def chunk_id(self) -> UUID: ...

    @property
    def text(self) -> str: ...

    @property
    def rank(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RerankerConfiguration:
    enabled: bool = False
    input_candidate_count: int = 20
    final_count: int = 10
    model_version: str | None = None

    def __post_init__(self) -> None:
        if self.input_candidate_count < 1:
            raise ValueError("reranker input_candidate_count must be positive")
        if self.final_count < 1:
            raise ValueError("reranker final_count must be positive")
        if self.final_count > self.input_candidate_count:
            raise ValueError("reranker final_count cannot exceed input_candidate_count")

    @classmethod
    def from_snapshot(cls, value: Mapping[str, Any]) -> RerankerConfiguration:
        return cls(
            enabled=bool(value.get("enabled", False)),
            input_candidate_count=int(value.get("input_candidate_count", 20)),
            final_count=int(value.get("final_count", 10)),
            model_version=(
                str(value["model_version"]) if value.get("model_version") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    """A retrieval candidate plus new reranking fields; prior ranks stay nested intact."""

    candidate: RerankableCandidate
    reranked_rank: int | None = None
    reranker_score: float | None = None

    @property
    def chunk_id(self) -> UUID:
        return self.candidate.chunk_id

    @property
    def text(self) -> str:
        return self.candidate.text

    @property
    def final_rank(self) -> int:
        return self.reranked_rank if self.reranked_rank is not None else self.candidate.rank


@dataclass(frozen=True, slots=True)
class RerankingExecution:
    enabled: bool
    candidates: tuple[RerankedCandidate, ...]
    input_candidate_count: int
    output_candidate_count: int
    provider_id: str | None
    model_id: str | None
    model_version: str | None
    latency_ms: float
    metadata: Mapping[str, Any]


class RerankingError(RuntimeError):
    """Safe stage error that does not expose provider request details or secrets."""


class RerankingService:
    def __init__(self, provider: RerankerProvider | None = None) -> None:
        self.provider = provider

    def execute(
        self,
        *,
        query: str,
        candidates: Sequence[RerankableCandidate],
        configuration: RerankerConfiguration,
    ) -> RerankingExecution:
        ordered = sorted(candidates, key=lambda value: (value.rank, str(value.chunk_id)))
        if not configuration.enabled:
            untouched = tuple(RerankedCandidate(candidate=value) for value in ordered)
            return RerankingExecution(
                enabled=False,
                candidates=untouched,
                input_candidate_count=len(ordered),
                output_candidate_count=len(untouched),
                provider_id=None,
                model_id=None,
                model_version=None,
                latency_ms=0.0,
                metadata={"reason": "reranking_disabled"},
            )
        if self.provider is None:
            raise RerankingError("Reranking is enabled but no provider is configured")

        selected_input = ordered[: configuration.input_candidate_count]
        selected_ids = [candidate.chunk_id for candidate in selected_input]
        if len(selected_ids) != len(set(selected_ids)):
            raise RerankingError("Reranker input contains duplicate candidates")
        if not selected_input:
            return RerankingExecution(
                enabled=True,
                candidates=(),
                input_candidate_count=0,
                output_candidate_count=0,
                provider_id=self.provider.provider_id,
                model_id=self.provider.model_id,
                model_version=configuration.model_version,
                latency_ms=0.0,
                metadata={"reason": "no_retrieved_candidates"},
            )
        provider_candidates = [
            RerankCandidate(
                candidate_id=str(candidate.chunk_id),
                text=candidate.text,
                original_rank=candidate.rank,
                metadata={"chunk_id": str(candidate.chunk_id)},
            )
            for candidate in selected_input
        ]
        started = perf_counter()
        try:
            results = self.provider.rerank(
                query,
                provider_candidates,
                top_k=configuration.final_count,
            )
        except Exception as exc:
            raise RerankingError("Reranker provider failed") from exc
        latency_ms = (perf_counter() - started) * 1000

        by_id = {str(candidate.chunk_id): candidate for candidate in selected_input}
        original_ranks = {str(candidate.chunk_id): candidate.rank for candidate in selected_input}
        result_ids = [result.candidate_id for result in results]
        if len(result_ids) != len(set(result_ids)):
            raise RerankingError("Reranker returned duplicate candidates")
        if any(candidate_id not in by_id for candidate_id in result_ids):
            raise RerankingError("Reranker returned a candidate outside its input set")
        if len(results) > configuration.final_count:
            raise RerankingError("Reranker returned more than the configured final count")
        expected_count = min(configuration.final_count, len(selected_input))
        if len(results) != expected_count:
            raise RerankingError("Reranker returned an unexpected candidate count")
        if any(result.original_rank != original_ranks[result.candidate_id] for result in results):
            raise RerankingError("Reranker changed an original retrieval rank")
        expected_ranks = list(range(1, len(results) + 1))
        actual_ranks = sorted(result.reranked_rank for result in results)
        if actual_ranks != expected_ranks:
            raise RerankingError("Reranker returned invalid reranked ranks")

        reranked = tuple(
            RerankedCandidate(
                candidate=by_id[result.candidate_id],
                reranked_rank=result.reranked_rank,
                reranker_score=result.score,
            )
            for result in sorted(results, key=lambda value: value.reranked_rank)
        )
        return RerankingExecution(
            enabled=True,
            candidates=reranked,
            input_candidate_count=len(selected_input),
            output_candidate_count=len(reranked),
            provider_id=self.provider.provider_id,
            model_id=self.provider.model_id,
            model_version=configuration.model_version,
            latency_ms=latency_ms,
            metadata={
                "configured_input_candidate_count": configuration.input_candidate_count,
                "configured_final_count": configuration.final_count,
            },
        )


def persist_reranking_results(
    session: Session,
    *,
    query_run_id: UUID,
    execution: RerankingExecution,
) -> None:
    """Attach reranker fields without overwriting lexical/dense/fusion ranks or scores."""

    if not execution.enabled:
        return
    by_chunk_id = {candidate.chunk_id: candidate for candidate in execution.candidates}
    if not by_chunk_id:
        return
    records = session.scalars(
        select(RetrievalResultRecord).where(
            RetrievalResultRecord.query_run_id == query_run_id,
            RetrievalResultRecord.chunk_id.in_(by_chunk_id),
        )
    ).all()
    for record in records:
        candidate = by_chunk_id[record.chunk_id]
        record.reranked_rank = candidate.reranked_rank
        record.reranker_score = candidate.reranker_score
        metadata = dict(record.result_metadata or {})
        metadata["reranker"] = {
            "provider_id": execution.provider_id,
            "model_id": execution.model_id,
            "model_version": execution.model_version,
            "latency_ms": execution.latency_ms,
        }
        record.result_metadata = metadata
    session.flush()
