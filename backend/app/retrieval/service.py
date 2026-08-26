"""Fixed-mode retrieval service built over Chunk 1 indexes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Chunk,
    CorpusVersion,
    CorpusVersionStatus,
    RetrievalMode,
    SourceDocument,
)
from backend.app.indexing.errors import CorpusVersionNotFound, IndexNotReady
from backend.app.indexing.schemas import RetrievalResult, SearchFilters
from backend.app.indexing.service import IndexingService

from .contracts import (
    MetadataFilters,
    RetrievalExecution,
    RetrievalRequest,
    RetrievalStageRecord,
    RetrievedCandidate,
    Retriever,
)
from .fusion import min_max_normalize, reciprocal_rank_fusion


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


class RetrievalStageObserver(Protocol):
    """Optional application-layer hook used for observable stage boundaries."""

    def __call__(self, stage: str, event: str, details: Mapping[str, Any]) -> None: ...


class LexicalIndexRetriever:
    retriever_type = "lexical"

    def __init__(self, indexing: IndexingService) -> None:
        self.indexing = indexing

    def retrieve(
        self,
        corpus_version_id: UUID,
        query: str,
        *,
        candidate_count: int,
        filters: SearchFilters | None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Sequence[RetrievalResult]:
        return self.indexing.lexical_search(
            corpus_version_id, query, top_k=candidate_count, filters=filters
        )


class DenseIndexRetriever:
    retriever_type = "dense"

    def __init__(self, indexing: IndexingService) -> None:
        self.indexing = indexing

    def retrieve(
        self,
        corpus_version_id: UUID,
        query: str,
        *,
        candidate_count: int,
        filters: SearchFilters | None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Sequence[RetrievalResult]:
        return self.indexing.dense_search(
            corpus_version_id, query, top_k=candidate_count, filters=filters
        )


class RetrievalService:
    def __init__(
        self,
        session: Session,
        indexing: IndexingService,
        *,
        lexical_retriever: Retriever | None = None,
        dense_retriever: Retriever | None = None,
    ) -> None:
        self.session = session
        self.lexical = lexical_retriever or LexicalIndexRetriever(indexing)
        self.dense = dense_retriever or DenseIndexRetriever(indexing)

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        observer: RetrievalStageObserver | None = None,
    ) -> RetrievalExecution:
        started = perf_counter()
        version = self.session.get(CorpusVersion, request.corpus_version_id)
        if version is None:
            raise CorpusVersionNotFound(
                f"corpus version does not exist: {request.corpus_version_id}"
            )
        if version.status != CorpusVersionStatus.READY:
            raise IndexNotReady("corpus version is not ready for retrieval")
        if request.mode == RetrievalMode.NONE:
            return RetrievalExecution(
                mode=request.mode,
                retrieval_disabled=True,
                total_timing_ms=_elapsed_ms(started),
            )

        search_filters, has_matches = self._resolve_filters(
            request.corpus_version_id, request.filters
        )
        if not has_matches:
            return RetrievalExecution(mode=request.mode, total_timing_ms=_elapsed_ms(started))

        lexical: Sequence[RetrievalResult] = ()
        dense: Sequence[RetrievalResult] = ()
        lexical_ms: int | None = None
        dense_ms: int | None = None
        if request.mode in {RetrievalMode.LEXICAL, RetrievalMode.HYBRID}:
            stage_started = perf_counter()
            self._observe(
                observer,
                "lexical",
                "started",
                {
                    "candidate_count": request.lexical.candidate_count,
                },
            )
            try:
                lexical = self.lexical.retrieve(
                    request.corpus_version_id,
                    request.query,
                    candidate_count=request.lexical.candidate_count,
                    filters=search_filters,
                    configuration=None,
                )
            except Exception as exc:
                self._observe(
                    observer,
                    "lexical",
                    "failed",
                    {
                        "exception_type": type(exc).__name__,
                    },
                )
                raise
            lexical_ms = _elapsed_ms(stage_started)
            self._observe(
                observer,
                "lexical",
                "succeeded",
                {
                    "candidate_count": len(lexical),
                    "latency_ms": lexical_ms,
                },
            )
        if request.mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}:
            stage_started = perf_counter()
            self._observe(
                observer,
                "dense",
                "started",
                {
                    "candidate_count": request.dense.candidate_count,
                },
            )
            try:
                dense = self.dense.retrieve(
                    request.corpus_version_id,
                    request.query,
                    candidate_count=request.dense.candidate_count,
                    filters=search_filters,
                    configuration=None,
                )
            except Exception as exc:
                self._observe(
                    observer,
                    "dense",
                    "failed",
                    {
                        "exception_type": type(exc).__name__,
                    },
                )
                raise
            dense_ms = _elapsed_ms(stage_started)
            self._observe(
                observer,
                "dense",
                "succeeded",
                {
                    "candidate_count": len(dense),
                    "latency_ms": dense_ms,
                },
            )

        lexical_normalized = min_max_normalize(lexical)
        dense_normalized = min_max_normalize(dense)
        records = self._stage_records(
            lexical, dense, lexical_normalized, dense_normalized, lexical_ms, dense_ms
        )
        candidates = self._candidates(
            request.corpus_version_id,
            lexical,
            dense,
            lexical_normalized,
            dense_normalized,
        )
        fusion_ms: int | None = None
        if request.mode == RetrievalMode.HYBRID:
            fusion_started = perf_counter()
            self._observe(
                observer,
                "fusion",
                "started",
                {
                    "lexical_candidate_count": len(lexical),
                    "dense_candidate_count": len(dense),
                    "top_k": request.top_k,
                },
            )
            try:
                fused = reciprocal_rank_fusion(
                    lexical, dense, configuration=request.fusion, top_k=request.top_k
                )
            except Exception as exc:
                self._observe(
                    observer,
                    "fusion",
                    "failed",
                    {
                        "exception_type": type(exc).__name__,
                    },
                )
                raise
            fusion_ms = _elapsed_ms(fusion_started)
            self._observe(
                observer,
                "fusion",
                "succeeded",
                {
                    "candidate_count": len(fused),
                    "latency_ms": fusion_ms,
                },
            )
            by_id = {str(candidate.chunk_id): candidate for candidate in candidates}
            candidates = [
                replace(
                    by_id[item.chunk_id],
                    fused_rank=item.rank,
                    fusion_score=item.score,
                    rrf_contributions={
                        "lexical": item.lexical_contribution,
                        "dense": item.dense_contribution,
                    },
                )
                for item in fused
            ]
            records.extend(
                RetrievalStageRecord(
                    chunk_id=UUID(item.chunk_id),
                    retriever_type="hybrid",
                    fused_rank=item.rank,
                    fusion_score=item.score,
                    timing_ms=fusion_ms,
                    metadata={
                        "rrf_lexical_contribution": item.lexical_contribution,
                        "rrf_dense_contribution": item.dense_contribution,
                        "rrf_rank_constant": request.fusion.rank_constant,
                        "rrf_lexical_weight": request.fusion.lexical_weight,
                        "rrf_dense_weight": request.fusion.dense_weight,
                    },
                )
                for item in fused
            )
        else:
            candidates.sort(key=lambda candidate: (candidate.rank, str(candidate.chunk_id)))
            candidates = candidates[: request.top_k]
        return RetrievalExecution(
            mode=request.mode,
            candidates=tuple(candidates),
            records=tuple(records),
            total_timing_ms=_elapsed_ms(started),
            lexical_timing_ms=lexical_ms,
            dense_timing_ms=dense_ms,
            fusion_timing_ms=fusion_ms,
        )

    @staticmethod
    def _observe(
        observer: RetrievalStageObserver | None,
        stage: str,
        event: str,
        details: Mapping[str, Any],
    ) -> None:
        if observer is not None:
            observer(stage, event, details)

    def _resolve_filters(
        self, corpus_version_id: UUID, filters: MetadataFilters
    ) -> tuple[SearchFilters | None, bool]:
        if not filters.document_ids and not filters.publication_years:
            return None, True
        statement = select(SourceDocument.id).where(
            SourceDocument.corpus_version_id == corpus_version_id
        )
        if filters.document_ids:
            statement = statement.where(SourceDocument.id.in_(filters.document_ids))
        if filters.publication_years:
            statement = statement.where(
                SourceDocument.publication_year.in_(filters.publication_years)
            )
        document_ids = frozenset(str(value) for value in self.session.scalars(statement))
        if not document_ids:
            return None, False
        return SearchFilters(document_ids=document_ids), True

    def _candidates(
        self,
        corpus_version_id: UUID,
        lexical: Sequence[RetrievalResult],
        dense: Sequence[RetrievalResult],
        lexical_normalized: Mapping[str, float],
        dense_normalized: Mapping[str, float],
    ) -> list[RetrievedCandidate]:
        raw_by_id = {item.chunk_id: item for item in [*lexical, *dense]}
        chunk_ids = [UUID(value) for value in raw_by_id]
        chunks = {
            chunk.id: chunk
            for chunk in self.session.scalars(
                select(Chunk).where(
                    Chunk.corpus_version_id == corpus_version_id,
                    Chunk.id.in_(chunk_ids),
                )
            )
        }
        lexical_by_id = {item.chunk_id: item for item in lexical}
        dense_by_id = {item.chunk_id: item for item in dense}
        result: list[RetrievedCandidate] = []
        for chunk_id, raw in raw_by_id.items():
            chunk = chunks.get(UUID(chunk_id))
            if chunk is None:
                continue
            lexical_item = lexical_by_id.get(chunk_id)
            dense_item = dense_by_id.get(chunk_id)
            result.append(
                RetrievedCandidate(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    text=raw.text,
                    metadata=raw.metadata,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    lexical_rank=lexical_item.rank if lexical_item else None,
                    lexical_score=lexical_item.score if lexical_item else None,
                    lexical_normalized_score=lexical_normalized.get(chunk_id),
                    dense_rank=dense_item.rank if dense_item else None,
                    dense_score=dense_item.score if dense_item else None,
                    dense_normalized_score=dense_normalized.get(chunk_id),
                )
            )
        return result

    @staticmethod
    def _stage_records(
        lexical: Sequence[RetrievalResult],
        dense: Sequence[RetrievalResult],
        lexical_normalized: Mapping[str, float],
        dense_normalized: Mapping[str, float],
        lexical_ms: int | None,
        dense_ms: int | None,
    ) -> list[RetrievalStageRecord]:
        return [
            RetrievalStageRecord(
                chunk_id=UUID(item.chunk_id),
                retriever_type="lexical",
                original_rank=item.rank,
                original_score=item.score,
                normalized_score=lexical_normalized[item.chunk_id],
                timing_ms=lexical_ms,
                metadata={"retrieval_method": item.retrieval_method},
            )
            for item in lexical
        ] + [
            RetrievalStageRecord(
                chunk_id=UUID(item.chunk_id),
                retriever_type="dense",
                original_rank=item.rank,
                original_score=item.score,
                normalized_score=dense_normalized[item.chunk_id],
                timing_ms=dense_ms,
                metadata={"retrieval_method": item.retrieval_method},
            )
            for item in dense
        ]
