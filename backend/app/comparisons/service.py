from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Chunk,
    Citation,
    ContextSource,
    CorpusVersion,
    CorpusVersionStatus,
    GeneratedClaim,
    PipelineConfiguration,
    QueryComparison,
    QueryComparisonRun,
    QueryRun,
    QueryRunStatus,
    RetrievalResultRecord,
    SourceDocument,
    TraceSpan,
)
from backend.app.query_runtime.schemas import QueryRunCreate
from backend.app.query_runtime.service import QueryOrchestrator

from .schemas import (
    ComparisonCitation,
    ComparisonClaim,
    ComparisonColumn,
    ComparisonContextSource,
    ComparisonPipeline,
    ComparisonStage,
    ConfigurationFacet,
    EvidenceCell,
    EvidenceOverlap,
    EvidenceRow,
    QueryComparisonCreate,
    QueryComparisonRead,
)

ComparisonRunner = Callable[[QueryRunCreate], QueryRun]


@dataclass(slots=True)
class _RankCell:
    lexical_rank: int | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    fused_rank: int | None = None
    fusion_score: float | None = None
    reranked_rank: int | None = None
    reranker_score: float | None = None
    selected_for_context: bool = False


class QueryComparisonService:
    """Run and align controlled comparisons without changing the query runtime."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        payload: QueryComparisonCreate,
        *,
        runner: ComparisonRunner | None = None,
    ) -> QueryComparisonRead:
        pipelines = self._validate(payload)
        comparison = QueryComparison(
            corpus_version_id=payload.corpus_version_id,
            original_question=payload.question,
            status="running",
        )
        self.session.add(comparison)
        self.session.flush()
        self.session.commit()
        self.session.refresh(comparison)

        execute = runner or (
            lambda request: QueryOrchestrator(self.session).execute(
                request, raise_on_failure=False
            )
        )
        failed_runs = 0
        for position, pipeline in enumerate(pipelines):
            invoked_at = datetime.now(UTC)
            try:
                run = execute(
                    QueryRunCreate(
                        corpus_version_id=payload.corpus_version_id,
                        pipeline_configuration_id=pipeline.id,
                        query_text=payload.question,
                        filters=payload.filters,
                    )
                )
            except DomainError as error:
                # QueryOrchestrator deliberately persists an inspectable failed run
                # before raising. Resolve that durable run and retain it as a column.
                failed_run = self._find_run_after(
                    payload=payload,
                    pipeline_id=pipeline.id,
                    invoked_at=invoked_at,
                )
                if failed_run is None:
                    comparison.status = "failed"
                    comparison.finished_at = datetime.now(UTC)
                    comparison.failure_message = (
                        "A pipeline failed before an inspectable QueryRun was persisted."
                    )
                    self.session.commit()
                    raise DomainError(
                        "COMPARISON_RUN_FAILED",
                        comparison.failure_message,
                        status_code=503,
                    ) from error
                run = failed_run
            if run.corpus_version_id != payload.corpus_version_id:
                self._fail_invariant(comparison, "A compared run used a different corpus version.")
            if run.query_text != payload.question:
                self._fail_invariant(
                    comparison, "A compared run used a different original question."
                )
            if run.pipeline_configuration_id != pipeline.id:
                self._fail_invariant(comparison, "A compared run used a different pipeline.")
            self.session.add(
                QueryComparisonRun(
                    comparison_id=comparison.id,
                    query_run_id=run.id,
                    pipeline_configuration_id=pipeline.id,
                    column_position=position,
                )
            )
            if run.status == QueryRunStatus.FAILED:
                failed_runs += 1
            self.session.flush()

        comparison.status = "completed"
        comparison.finished_at = datetime.now(UTC)
        if failed_runs:
            comparison.failure_message = (
                f"{failed_runs} of {len(pipelines)} pipeline runs failed; "
                "successful columns remain comparable."
            )
        self.session.commit()
        return self.get(comparison.id)

    def get(self, comparison_id: UUID) -> QueryComparisonRead:
        comparison = self.session.get(QueryComparison, comparison_id)
        if comparison is None:
            raise DomainError(
                "QUERY_COMPARISON_NOT_FOUND",
                "Query comparison not found.",
                status_code=404,
            )
        members = list(
            self.session.execute(
                select(QueryComparisonRun, QueryRun, PipelineConfiguration)
                .join(QueryRun, QueryRun.id == QueryComparisonRun.query_run_id)
                .join(
                    PipelineConfiguration,
                    PipelineConfiguration.id
                    == QueryComparisonRun.pipeline_configuration_id,
                )
                .where(QueryComparisonRun.comparison_id == comparison.id)
                .order_by(QueryComparisonRun.column_position)
            ).tuples()
        )
        run_ids = [run.id for _, run, _ in members]
        if not run_ids:
            return QueryComparisonRead(
                id=comparison.id,
                corpus_version_id=comparison.corpus_version_id,
                original_question=comparison.original_question,
                status=_value(comparison.status),
                created_at=comparison.created_at,
                finished_at=comparison.finished_at,
                failure_message=comparison.failure_message,
                configuration_differences=[],
                evidence_overlap=[],
                evidence_rows=[],
                columns=[],
            )

        version = self.session.get(CorpusVersion, comparison.corpus_version_id)
        embedding_configuration = version.embedding_configuration if version else {}
        retrieval_rows = self._retrieval_rows(run_ids)
        context_rows = self._context_rows(run_ids)
        claim_rows = self._claim_rows(run_ids)
        citation_rows = self._citation_rows(run_ids)
        trace_rows = self._trace_rows(run_ids)

        contexts_by_run: dict[UUID, list[tuple[ContextSource, Chunk, SourceDocument]]] = {}
        context_by_run_chunk: dict[tuple[UUID, UUID], ContextSource] = {}
        for source, chunk, document in context_rows:
            contexts_by_run.setdefault(source.query_run_id, []).append(
                (source, chunk, document)
            )
            context_by_run_chunk[(source.query_run_id, source.chunk_id)] = source

        citations_by_claim: dict[UUID, list[Citation]] = {}
        for citation in citation_rows:
            citations_by_claim.setdefault(citation.claim_id, []).append(citation)
        claims_by_run: dict[UUID, list[GeneratedClaim]] = {}
        for claim in claim_rows:
            claims_by_run.setdefault(claim.query_run_id, []).append(claim)
        traces_by_run: dict[UUID, list[TraceSpan]] = {}
        for span in trace_rows:
            traces_by_run.setdefault(span.query_run_id, []).append(span)

        facets_by_pipeline = {
            pipeline.id: _configuration_facets(pipeline, embedding_configuration)
            for _, _, pipeline in members
        }
        columns = [
            self._column(
                member=member,
                facets=facets_by_pipeline[member[2].id],
                contexts=contexts_by_run.get(member[1].id, []),
                claims=claims_by_run.get(member[1].id, []),
                citations_by_claim=citations_by_claim,
                traces=traces_by_run.get(member[1].id, []),
            )
            for member in members
        ]
        evidence_rows, retrieved_by_run = _align_evidence(
            members=members,
            retrieval_rows=retrieval_rows,
            context_by_run_chunk=context_by_run_chunk,
        )
        return QueryComparisonRead(
            id=comparison.id,
            corpus_version_id=comparison.corpus_version_id,
            original_question=comparison.original_question,
            status=_value(comparison.status),
            created_at=comparison.created_at,
            finished_at=comparison.finished_at,
            failure_message=comparison.failure_message,
            configuration_differences=_configuration_differences(
                [facets_by_pipeline[pipeline.id] for _, _, pipeline in members]
            ),
            evidence_overlap=_overlaps(run_ids, retrieved_by_run),
            evidence_rows=evidence_rows,
            columns=columns,
        )

    def _validate(
        self, payload: QueryComparisonCreate
    ) -> list[PipelineConfiguration]:
        version = self.session.get(CorpusVersion, payload.corpus_version_id)
        if version is None or version.status != CorpusVersionStatus.READY:
            raise DomainError(
                "CORPUS_VERSION_NOT_READY",
                "Comparisons require an existing ready corpus version.",
                status_code=409,
            )
        found = list(
            self.session.scalars(
                select(PipelineConfiguration).where(
                    PipelineConfiguration.id.in_(payload.pipeline_configuration_ids)
                )
            )
        )
        by_id = {pipeline.id: pipeline for pipeline in found}
        if len(by_id) != len(payload.pipeline_configuration_ids):
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Every selected pipeline configuration must exist.",
                status_code=404,
            )
        pipelines = [by_id[pipeline_id] for pipeline_id in payload.pipeline_configuration_ids]
        if any(not pipeline.is_frozen for pipeline in pipelines):
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Comparisons require frozen pipeline configurations.",
                status_code=409,
            )
        return pipelines

    def _find_run_after(
        self,
        *,
        payload: QueryComparisonCreate,
        pipeline_id: UUID,
        invoked_at: datetime,
    ) -> QueryRun | None:
        return self.session.scalar(
            select(QueryRun)
            .where(
                QueryRun.corpus_version_id == payload.corpus_version_id,
                QueryRun.pipeline_configuration_id == pipeline_id,
                QueryRun.query_text == payload.question,
                QueryRun.created_at >= invoked_at,
            )
            .order_by(QueryRun.created_at.desc())
            .limit(1)
        )

    def _fail_invariant(self, comparison: QueryComparison, message: str) -> None:
        comparison.status = "failed"
        comparison.finished_at = datetime.now(UTC)
        comparison.failure_message = message
        self.session.commit()
        raise DomainError("COMPARISON_INVARIANT_VIOLATION", message, status_code=500)

    def _retrieval_rows(
        self, run_ids: list[UUID]
    ) -> list[tuple[RetrievalResultRecord, Chunk, SourceDocument]]:
        return list(
            self.session.execute(
                select(RetrievalResultRecord, Chunk, SourceDocument)
                .join(Chunk, Chunk.id == RetrievalResultRecord.chunk_id)
                .join(SourceDocument, SourceDocument.id == Chunk.document_id)
                .where(RetrievalResultRecord.query_run_id.in_(run_ids))
                .order_by(
                    RetrievalResultRecord.query_run_id,
                    RetrievalResultRecord.retriever_type,
                    RetrievalResultRecord.original_rank,
                )
            ).tuples()
        )

    def _context_rows(
        self, run_ids: list[UUID]
    ) -> list[tuple[ContextSource, Chunk, SourceDocument]]:
        return list(
            self.session.execute(
                select(ContextSource, Chunk, SourceDocument)
                .join(Chunk, Chunk.id == ContextSource.chunk_id)
                .join(SourceDocument, SourceDocument.id == ContextSource.document_id)
                .where(ContextSource.query_run_id.in_(run_ids))
                .order_by(ContextSource.query_run_id, ContextSource.sequence_number)
            ).tuples()
        )

    def _claim_rows(self, run_ids: list[UUID]) -> list[GeneratedClaim]:
        return list(
            self.session.scalars(
                select(GeneratedClaim)
                .where(GeneratedClaim.query_run_id.in_(run_ids))
                .order_by(GeneratedClaim.query_run_id, GeneratedClaim.sequence_number)
            )
        )

    def _citation_rows(self, run_ids: list[UUID]) -> list[Citation]:
        return list(
            self.session.scalars(
                select(Citation)
                .where(Citation.query_run_id.in_(run_ids))
                .order_by(Citation.query_run_id, Citation.citation_id)
            )
        )

    def _trace_rows(self, run_ids: list[UUID]) -> list[TraceSpan]:
        return list(
            self.session.scalars(
                select(TraceSpan)
                .where(TraceSpan.query_run_id.in_(run_ids))
                .order_by(TraceSpan.query_run_id, TraceSpan.sequence_number)
            )
        )

    @staticmethod
    def _column(
        *,
        member: tuple[QueryComparisonRun, QueryRun, PipelineConfiguration],
        facets: dict[str, Any],
        contexts: list[tuple[ContextSource, Chunk, SourceDocument]],
        claims: list[GeneratedClaim],
        citations_by_claim: dict[UUID, list[Citation]],
        traces: list[TraceSpan],
    ) -> ComparisonColumn:
        link, run, pipeline = member
        timeline = [
            ComparisonStage(
                sequence_number=span.sequence_number,
                name=span.name,
                span_type=_value(span.span_type),
                status=_value(span.status),
                latency_ms=span.latency_ms,
                error_code=span.error_code,
            )
            for span in traces
        ]
        stage_latency = {
            f"{span.sequence_number}:{span.name}": span.latency_ms
            for span in traces
            if span.latency_ms is not None
        }
        return ComparisonColumn(
            position=link.column_position,
            query_run_id=run.id,
            pipeline=ComparisonPipeline(
                id=pipeline.id,
                name=pipeline.name,
                version=pipeline.version,
                configuration_hash=pipeline.configuration_hash,
                retrieval_mode=_value(pipeline.retrieval_mode),
                facets=facets,
                frozen_snapshot=_frozen_snapshot(pipeline),
            ),
            run_status=_value(run.status),
            original_query=run.query_text,
            rewritten_query=run.rewritten_query,
            configured_route=run.route_decision,
            classification=run.classification,
            answerability=(
                _value(run.answerability_decision)
                if run.answerability_decision is not None
                else None
            ),
            answer=run.answer_text,
            limitations=run.limitations,
            abstention_reason=run.abstention_reason,
            context_artifact_id=run.context_artifact_id,
            context_sources=[
                ComparisonContextSource(
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    document_title=document.title,
                    citation_id=source.citation_id,
                    sequence_number=source.sequence_number,
                    selected=source.selected,
                    exclusion_reason=source.exclusion_reason,
                    token_count=source.token_count,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    text=chunk.text,
                )
                for source, chunk, document in contexts
            ],
            claims=[
                ComparisonClaim(
                    sequence_number=claim.sequence_number,
                    text=claim.claim_text,
                    support_status=_value(claim.support_status),
                    citations=[
                        ComparisonCitation(
                            citation_id=citation.citation_id,
                            chunk_id=citation.chunk_id,
                            document_id=citation.document_id,
                            page_number=citation.page_number,
                            referenced_text=citation.referenced_text,
                        )
                        for citation in citations_by_claim.get(claim.id, [])
                    ],
                )
                for claim in claims
            ],
            total_latency_ms=run.total_latency_ms,
            stage_latency_ms=stage_latency,
            timeline=timeline,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            estimated_cost=run.estimated_cost,
            cost_currency=pipeline.generation_configuration.get("cost_currency"),
            failure_code=run.failure_code,
            failure_message=run.failure_message,
        )


def _value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _frozen_snapshot(pipeline: PipelineConfiguration) -> dict[str, Any]:
    return {
        "retrieval_mode": _value(pipeline.retrieval_mode),
        "lexical": pipeline.lexical_configuration,
        "dense": pipeline.dense_configuration,
        "fusion": pipeline.fusion_configuration,
        "reranker": pipeline.reranker_configuration,
        "query_processing": pipeline.query_processing_configuration,
        "context": pipeline.context_configuration,
        "generation": pipeline.generation_configuration,
        "citation": pipeline.citation_configuration,
        "prompt_versions": pipeline.prompt_versions,
        "configuration_hash": pipeline.configuration_hash,
    }


def _configuration_facets(
    pipeline: PipelineConfiguration, embedding_configuration: dict[str, Any]
) -> dict[str, Any]:
    lexical = pipeline.lexical_configuration
    dense = pipeline.dense_configuration
    fusion = pipeline.fusion_configuration
    reranker = pipeline.reranker_configuration
    context = pipeline.context_configuration
    generation = pipeline.generation_configuration
    return {
        "retrieval_mode": _value(pipeline.retrieval_mode),
        "lexical_top_k": lexical.get("top_k"),
        "dense_top_k": dense.get("top_k"),
        "candidate_count": max(
            int(lexical.get("candidate_count", 0)),
            int(dense.get("candidate_count", 0)),
        ),
        "embedding_model": embedding_configuration.get("model"),
        "fusion_method": fusion.get("method"),
        "fusion_rrf_k": fusion.get("rrf_k"),
        "fusion_lexical_weight": fusion.get("lexical_weight"),
        "fusion_dense_weight": fusion.get("dense_weight"),
        "fusion_final_count": fusion.get("final_count"),
        "reranker_enabled": reranker.get("enabled"),
        "reranker_model": reranker.get("model"),
        "reranker_input_count": reranker.get("input_candidate_count"),
        "reranker_final_count": reranker.get("final_count"),
        "context_token_budget": context.get("token_budget"),
        "context_deduplicate": context.get("deduplicate"),
        "generation_provider": generation.get("provider"),
        "generation_model": generation.get("model"),
        "generation_temperature": generation.get("temperature"),
        "generation_max_output_tokens": generation.get("max_output_tokens"),
        "prompt_version": pipeline.prompt_versions.get("grounded_generation"),
    }


_FACET_LABELS = {
    "retrieval_mode": "Retrieval mode",
    "lexical_top_k": "Lexical top-k",
    "dense_top_k": "Dense top-k",
    "candidate_count": "Candidate count",
    "embedding_model": "Embedding model",
    "fusion_method": "Fusion method",
    "fusion_rrf_k": "RRF rank constant",
    "fusion_lexical_weight": "Lexical fusion weight",
    "fusion_dense_weight": "Dense fusion weight",
    "fusion_final_count": "Fused result count",
    "reranker_enabled": "Reranker enabled",
    "reranker_model": "Reranker model",
    "reranker_input_count": "Reranker input count",
    "reranker_final_count": "Reranker final count",
    "context_token_budget": "Context token budget",
    "context_deduplicate": "Context deduplication",
    "generation_provider": "Generation provider",
    "generation_model": "Generation model",
    "generation_temperature": "Temperature",
    "generation_max_output_tokens": "Output token limit",
    "prompt_version": "Prompt version",
}


def _configuration_differences(
    configurations: list[dict[str, Any]],
) -> list[ConfigurationFacet]:
    differences: list[ConfigurationFacet] = []
    if not configurations:
        return differences
    for key in _FACET_LABELS:
        values = [configuration.get(key) for configuration in configurations]
        serialized = {repr(value) for value in values}
        if len(serialized) > 1:
            differences.append(
                ConfigurationFacet(key=key, label=_FACET_LABELS[key], values=values)
            )
    return differences


def _align_evidence(
    *,
    members: list[tuple[QueryComparisonRun, QueryRun, PipelineConfiguration]],
    retrieval_rows: list[tuple[RetrievalResultRecord, Chunk, SourceDocument]],
    context_by_run_chunk: dict[tuple[UUID, UUID], ContextSource],
) -> tuple[list[EvidenceRow], dict[UUID, set[UUID]]]:
    metadata: dict[UUID, tuple[Chunk, SourceDocument]] = {}
    cells: dict[tuple[UUID, UUID], _RankCell] = {}
    retrieved_by_run: dict[UUID, set[UUID]] = {run.id: set() for _, run, _ in members}
    for result, chunk, document in retrieval_rows:
        metadata[chunk.id] = (chunk, document)
        retrieved_by_run[result.query_run_id].add(chunk.id)
        cell = cells.setdefault((result.query_run_id, chunk.id), _RankCell())
        if result.retriever_type == "lexical":
            cell.lexical_rank = result.original_rank
            cell.lexical_score = result.original_score
        elif result.retriever_type == "dense":
            cell.dense_rank = result.original_rank
            cell.dense_score = result.original_score
        elif result.retriever_type == "hybrid":
            cell.fused_rank = result.fused_rank
            cell.fusion_score = result.fusion_score
        cell.reranked_rank = _minimum(cell.reranked_rank, result.reranked_rank)
        if result.reranker_score is not None:
            cell.reranker_score = result.reranker_score
        cell.selected_for_context = (
            cell.selected_for_context or result.selected_for_context
        )

    def best_rank(chunk_id: UUID) -> int:
        ranks = [
            rank
            for _, run, _ in members
            for rank in _cell_ranks(cells.get((run.id, chunk_id)))
            if rank is not None
        ]
        return min(ranks, default=1_000_000)

    output: list[EvidenceRow] = []
    for chunk_id in sorted(metadata, key=lambda value: (best_rank(value), str(value))):
        chunk, document = metadata[chunk_id]
        output_cells: list[EvidenceCell] = []
        for _, run, pipeline in members:
            aligned_cell = cells.get((run.id, chunk_id))
            source = context_by_run_chunk.get((run.id, chunk_id))
            output_cells.append(
                EvidenceCell(
                    query_run_id=run.id,
                    pipeline_configuration_id=pipeline.id,
                    present=aligned_cell is not None,
                    lexical_rank=aligned_cell.lexical_rank if aligned_cell else None,
                    lexical_score=aligned_cell.lexical_score if aligned_cell else None,
                    dense_rank=aligned_cell.dense_rank if aligned_cell else None,
                    dense_score=aligned_cell.dense_score if aligned_cell else None,
                    fused_rank=aligned_cell.fused_rank if aligned_cell else None,
                    fusion_score=aligned_cell.fusion_score if aligned_cell else None,
                    reranked_rank=(
                        aligned_cell.reranked_rank if aligned_cell else None
                    ),
                    reranker_score=(
                        aligned_cell.reranker_score if aligned_cell else None
                    ),
                    selected_for_context=(
                        aligned_cell.selected_for_context if aligned_cell else False
                    ),
                    exclusion_reason=source.exclusion_reason if source else None,
                    citation_id=source.citation_id if source else None,
                )
            )
        output.append(
            EvidenceRow(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=document.title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_path=chunk.section_path,
                text=chunk.text,
                cells=output_cells,
            )
        )
    return output, retrieved_by_run


def _minimum(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _cell_ranks(cell: _RankCell | None) -> list[int | None]:
    if cell is None:
        return []
    return [
        cell.reranked_rank,
        cell.fused_rank,
        cell.lexical_rank,
        cell.dense_rank,
    ]


def _overlaps(
    run_ids: list[UUID], retrieved_by_run: dict[UUID, set[UUID]]
) -> list[EvidenceOverlap]:
    output: list[EvidenceOverlap] = []
    for left, right in combinations(run_ids, 2):
        left_chunks = retrieved_by_run.get(left, set())
        right_chunks = retrieved_by_run.get(right, set())
        union = left_chunks | right_chunks
        output.append(
            EvidenceOverlap(
                left_query_run_id=left,
                right_query_run_id=right,
                shared_chunk_ids=sorted(left_chunks & right_chunks, key=str),
                left_only_chunk_ids=sorted(left_chunks - right_chunks, key=str),
                right_only_chunk_ids=sorted(right_chunks - left_chunks, key=str),
                jaccard=(len(left_chunks & right_chunks) / len(union) if union else None),
            )
        )
    return output
