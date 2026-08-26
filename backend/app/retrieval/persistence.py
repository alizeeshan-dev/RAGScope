"""Persistence adapter for inspectable, append-only retrieval stage ranks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import RetrievalResultRecord

from .contracts import RetrievalExecution


def persist_retrieval_results(
    session: Session, query_run_id: UUID, execution: RetrievalExecution
) -> list[RetrievalResultRecord]:
    """Idempotently insert stage rows without overwriting any earlier rank."""

    existing = {
        (row.chunk_id, row.retriever_type): row
        for row in session.scalars(
            select(RetrievalResultRecord).where(
                RetrievalResultRecord.query_run_id == query_run_id
            )
        )
    }
    persisted: list[RetrievalResultRecord] = []
    for stage in execution.records:
        key = (stage.chunk_id, stage.retriever_type)
        row = existing.get(key)
        if row is None:
            row = RetrievalResultRecord(
                query_run_id=query_run_id,
                chunk_id=stage.chunk_id,
                retriever_type=stage.retriever_type,
                original_rank=stage.original_rank,
                original_score=stage.original_score,
                normalized_score=stage.normalized_score,
                fused_rank=stage.fused_rank,
                fusion_score=stage.fusion_score,
                timing_ms=stage.timing_ms,
                result_metadata=dict(stage.metadata),
            )
            session.add(row)
            existing[key] = row
        persisted.append(row)
    session.flush()
    return persisted
