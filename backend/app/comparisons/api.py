from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db

from .schemas import QueryComparisonCreate, QueryComparisonRead
from .service import QueryComparisonService

router = APIRouter(tags=["query-comparisons"])


@router.post(
    "/query-comparisons",
    response_model=QueryComparisonRead,
    status_code=status.HTTP_201_CREATED,
)
def create_query_comparison(
    payload: QueryComparisonCreate,
    session: Annotated[Session, Depends(get_db)],
) -> QueryComparisonRead:
    return QueryComparisonService(session).create(payload)


@router.get("/query-comparisons/{comparison_id}", response_model=QueryComparisonRead)
def get_query_comparison(
    comparison_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> QueryComparisonRead:
    return QueryComparisonService(session).get(comparison_id)
