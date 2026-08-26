from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Chunk,
    Citation,
    ContextSource,
    GeneratedClaim,
    QueryRun,
    RetrievalResultRecord,
    SourceDocument,
)
from backend.app.db.session import get_db
from backend.app.query_runtime.schemas import (
    CitationRead,
    ClaimRead,
    ContextSourceRead,
    QueryRunCreate,
    QueryRunRead,
    RetrievalResultRead,
)
from backend.app.query_runtime.service import QueryOrchestrator
from backend.app.tracing.export import export_observable_trace
from backend.app.tracing.schemas import ObservableTraceExport

router = APIRouter(tags=["query-runs"])


def _run_or_404(session: Session, run_id: UUID) -> QueryRun:
    run = session.get(QueryRun, run_id)
    if run is None:
        raise DomainError("QUERY_RUN_NOT_FOUND", "Query run not found.", status_code=404)
    return run


@router.post("/query-runs", response_model=QueryRunRead, status_code=status.HTTP_201_CREATED)
def create_query_run(
    payload: QueryRunCreate,
    session: Annotated[Session, Depends(get_db)],
) -> QueryRun:
    # Stage failures are persisted research records. Returning the failed run ID lets
    # the Query Laboratory inspect its partial observable trace.
    return QueryOrchestrator(session).execute(payload, raise_on_failure=False)


@router.get("/query-runs/{run_id}", response_model=QueryRunRead)
def get_query_run(run_id: UUID, session: Annotated[Session, Depends(get_db)]) -> QueryRun:
    return _run_or_404(session, run_id)


@router.get("/query-runs/{run_id}/trace", response_model=ObservableTraceExport)
def get_query_run_trace(
    run_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> ObservableTraceExport:
    _run_or_404(session, run_id)
    return export_observable_trace(session, run_id)


@router.get(
    "/query-runs/{run_id}/retrieval-results",
    response_model=list[RetrievalResultRead],
)
def get_retrieval_results(
    run_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> list[RetrievalResultRead]:
    _run_or_404(session, run_id)
    rows = session.execute(
        select(RetrievalResultRecord, Chunk, SourceDocument)
        .join(Chunk, Chunk.id == RetrievalResultRecord.chunk_id)
        .join(SourceDocument, SourceDocument.id == Chunk.document_id)
        .where(RetrievalResultRecord.query_run_id == run_id)
        .order_by(RetrievalResultRecord.retriever_type, RetrievalResultRecord.original_rank)
    ).all()
    return [
        RetrievalResultRead(
            id=result.id,
            chunk_id=result.chunk_id,
            document_id=chunk.document_id,
            document_title=document.title,
            text=chunk.text,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_path=chunk.section_path,
            retriever_type=result.retriever_type,
            original_rank=result.original_rank,
            original_score=result.original_score,
            normalized_score=result.normalized_score,
            fused_rank=result.fused_rank,
            fusion_score=result.fusion_score,
            reranked_rank=result.reranked_rank,
            reranker_score=result.reranker_score,
            selected_for_context=result.selected_for_context,
            timing_ms=result.timing_ms,
            metadata=result.result_metadata,
        )
        for result, chunk, document in rows
    ]


@router.get("/query-runs/{run_id}/claims", response_model=list[ClaimRead])
def get_claims(run_id: UUID, session: Annotated[Session, Depends(get_db)]) -> list[ClaimRead]:
    _run_or_404(session, run_id)
    claims = list(
        session.scalars(
            select(GeneratedClaim)
            .where(GeneratedClaim.query_run_id == run_id)
            .order_by(GeneratedClaim.sequence_number)
        )
    )
    citations = list(
        session.scalars(
            select(Citation).where(Citation.query_run_id == run_id).order_by(Citation.citation_id)
        )
    )
    document_ids = {citation.document_id for citation in citations}
    document_titles = (
        {
            document.id: document.title
            for document in session.scalars(
                select(SourceDocument).where(SourceDocument.id.in_(document_ids))
            )
        }
        if document_ids
        else {}
    )
    by_claim: dict[UUID, list[Citation]] = {}
    for citation in citations:
        by_claim.setdefault(citation.claim_id, []).append(citation)
    return [
        ClaimRead(
            id=claim.id,
            sequence_number=claim.sequence_number,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            citation_ids=claim.citation_ids,
            support_status=claim.support_status.value,
            verification_method=claim.verification_method,
            verification_score=claim.verification_score,
            citations=[
                CitationRead(
                    id=item.id,
                    citation_id=item.citation_id,
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    document_title=document_titles.get(item.document_id),
                    page_number=item.page_number,
                    referenced_text=item.referenced_text,
                    entailment_status=item.entailment_status,
                    entailment_score=item.entailment_score,
                )
                for item in by_claim.get(claim.id, [])
            ],
        )
        for claim in claims
    ]


@router.get("/query-runs/{run_id}/context", response_model=list[ContextSourceRead])
def get_context_sources(
    run_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> list[ContextSourceRead]:
    _run_or_404(session, run_id)
    rows = session.execute(
        select(ContextSource, Chunk, SourceDocument)
        .join(Chunk, Chunk.id == ContextSource.chunk_id)
        .join(SourceDocument, SourceDocument.id == Chunk.document_id)
        .where(ContextSource.query_run_id == run_id)
        .order_by(ContextSource.sequence_number)
    ).all()
    return [
        ContextSourceRead(
            id=source.id,
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
            section_path=chunk.section_path,
            text=chunk.text,
        )
        for source, chunk, document in rows
    ]
