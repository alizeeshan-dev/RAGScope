from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Chunk,
    ClaimSupportStatus,
    ContextSource,
    GeneratedClaim,
    QueryRun,
    RetrievalResultRecord,
    TraceSpan,
)

from .schemas import RankMovement, TraceSummary


def build_trace_summary(session: Session, query_run_id: UUID) -> TraceSummary:
    """Build bounded structured metrics without opening artifact payloads."""

    run = session.get(QueryRun, query_run_id)
    if run is None:
        raise LookupError("Query run does not exist")

    spans = session.scalars(
        select(TraceSpan)
        .where(TraceSpan.query_run_id == query_run_id)
        .order_by(TraceSpan.sequence_number)
    ).all()
    results = session.scalars(
        select(RetrievalResultRecord).where(
            RetrievalResultRecord.query_run_id == query_run_id
        )
    ).all()
    context = session.scalars(
        select(ContextSource).where(ContextSource.query_run_id == query_run_id)
    ).all()
    claims = session.scalars(
        select(GeneratedClaim).where(GeneratedClaim.query_run_id == query_run_id)
    ).all()

    latency: dict[str, int] = defaultdict(int)
    for span in spans:
        # Parent and child stage timings can overlap and therefore are not additive.
        # Exposing each observable stage is more useful than hiding child timings.
        if span.latency_ms is not None:
            latency[span.span_type] += span.latency_ms

    per_chunk: dict[UUID, list[RetrievalResultRecord]] = defaultdict(list)
    for result in results:
        per_chunk[result.chunk_id].append(result)
    movement = [_rank_movement(chunk_id, records) for chunk_id, records in per_chunk.items()]
    movement.sort(key=lambda item: (_last_rank(item), str(item.chunk_id)))

    document_ids = session.scalars(
        select(Chunk.document_id)
        .join(RetrievalResultRecord, RetrievalResultRecord.chunk_id == Chunk.id)
        .where(RetrievalResultRecord.query_run_id == query_run_id)
        .distinct()
        .order_by(Chunk.document_id)
    ).all()

    reranked_ids = {
        result.chunk_id for result in results if result.reranked_rank is not None
    }
    fused_ids = {result.chunk_id for result in results if result.fused_rank is not None}
    selected = sum(source.selected for source in context)
    excluded = len(context) - selected
    retrieval_ids = set(per_chunk)
    context_ids = {source.chunk_id for source in context}

    return TraceSummary(
        total_latency_ms=run.total_latency_ms,
        latency_by_stage_ms=dict(latency),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        estimated_cost=run.estimated_cost,
        retrieval_candidate_count=len(retrieval_ids),
        reranked_candidate_count=len(reranked_ids),
        selected_context_count=selected,
        excluded_context_count=excluded,
        retrieved_document_ids=list(document_ids),
        rank_movement=movement,
        evidence_flow={
            "retrieved": len(retrieval_ids),
            "after_fusion": len(fused_ids) if fused_ids else len(retrieval_ids),
            "after_reranking": len(reranked_ids) if reranked_ids else len(retrieval_ids),
            "considered_for_context": len(context_ids),
            "selected_for_context": selected,
            "lost_at_fusion": max(0, len(retrieval_ids) - len(fused_ids)) if fused_ids else 0,
            "lost_at_reranking": (
                max(0, len(fused_ids or retrieval_ids) - len(reranked_ids))
                if reranked_ids
                else 0
            ),
            "lost_at_context": max(0, len(context_ids) - selected),
            "excluded_deduplication": sum(
                source.exclusion_reason == "deduplication" for source in context
            ),
            "excluded_token_budget": sum(
                source.exclusion_reason == "token_budget" for source in context
            ),
        },
        unsupported_claim_count=sum(
            claim.support_status == ClaimSupportStatus.UNSUPPORTED for claim in claims
        ),
        not_evaluated_claim_count=sum(
            claim.support_status == ClaimSupportStatus.NOT_EVALUATED for claim in claims
        ),
        failure_stage=next(
            (span.span_type for span in spans if _enum_value(span.status) == "failed"),
            None,
        ),
    )


def _rank_movement(
    chunk_id: UUID, records: list[RetrievalResultRecord]
) -> RankMovement:
    original = min(
        (record.original_rank for record in records if record.original_rank is not None),
        default=None,
    )
    fused = next((record.fused_rank for record in records if record.fused_rank is not None), None)
    reranked = next(
        (record.reranked_rank for record in records if record.reranked_rank is not None),
        None,
    )
    before = fused if fused is not None else original
    return RankMovement(
        chunk_id=chunk_id,
        retrieval_rank=original,
        fused_rank=fused,
        reranked_rank=reranked,
        movement=(before - reranked if before is not None and reranked is not None else None),
        selected_for_context=any(record.selected_for_context for record in records),
    )


def _last_rank(item: RankMovement) -> int:
    return item.reranked_rank or item.fused_rank or item.retrieval_rank or 2**31


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))
