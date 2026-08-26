from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db

from .schemas import (
    BenchmarkCreate,
    BenchmarkQuestionCreate,
    BenchmarkQuestionRead,
    BenchmarkQuestionUpdate,
    BenchmarkRead,
    BenchmarkVersionCreate,
    BenchmarkVersionRead,
    EvidenceSetCreate,
    LeakageCheckRead,
)
from .service import BenchmarkService

router = APIRouter(tags=["benchmarks"])


@router.post("/benchmarks", response_model=BenchmarkRead, status_code=status.HTTP_201_CREATED)
def create_benchmark(
    payload: BenchmarkCreate, session: Annotated[Session, Depends(get_db)]
) -> BenchmarkRead:
    result = BenchmarkService(session).create_benchmark(payload)
    session.commit()
    return result


@router.get("/benchmarks", response_model=list[BenchmarkRead])
def list_benchmarks(
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[BenchmarkRead]:
    return BenchmarkService(session).list_benchmarks(offset=offset, limit=limit)


@router.get("/benchmarks/{benchmark_id}", response_model=BenchmarkRead)
def get_benchmark(
    benchmark_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> BenchmarkRead:
    return BenchmarkService(session).get_benchmark(benchmark_id)


@router.post(
    "/benchmarks/{benchmark_id}/versions",
    response_model=BenchmarkVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_benchmark_version(
    benchmark_id: UUID,
    payload: BenchmarkVersionCreate,
    session: Annotated[Session, Depends(get_db)],
) -> BenchmarkVersionRead:
    result = BenchmarkService(session).create_version(benchmark_id, payload)
    session.commit()
    return result


@router.get("/benchmarks/{benchmark_id}/versions", response_model=list[BenchmarkVersionRead])
def list_benchmark_versions(
    benchmark_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> list[BenchmarkVersionRead]:
    return BenchmarkService(session).list_versions(benchmark_id)


@router.get("/benchmark-versions/{version_id}", response_model=BenchmarkVersionRead)
def get_benchmark_version(
    version_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> BenchmarkVersionRead:
    return BenchmarkService(session).get_version(version_id)


@router.post(
    "/benchmark-versions/{version_id}/questions",
    response_model=BenchmarkQuestionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_benchmark_question(
    version_id: UUID,
    payload: BenchmarkQuestionCreate,
    session: Annotated[Session, Depends(get_db)],
) -> BenchmarkQuestionRead:
    result = BenchmarkService(session).create_question(version_id, payload)
    session.commit()
    return result


@router.get(
    "/benchmark-versions/{version_id}/questions", response_model=list[BenchmarkQuestionRead]
)
def list_benchmark_questions(
    version_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> list[BenchmarkQuestionRead]:
    return BenchmarkService(session).list_questions(version_id)


@router.get("/benchmark-questions/{question_id}", response_model=BenchmarkQuestionRead)
def get_benchmark_question(
    question_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> BenchmarkQuestionRead:
    return BenchmarkService(session).get_question(question_id)


@router.patch("/benchmark-questions/{question_id}", response_model=BenchmarkQuestionRead)
def update_benchmark_question(
    question_id: UUID,
    payload: BenchmarkQuestionUpdate,
    session: Annotated[Session, Depends(get_db)],
) -> BenchmarkQuestionRead:
    result = BenchmarkService(session).update_question(question_id, payload)
    session.commit()
    return result


@router.post(
    "/benchmark-questions/{question_id}/evidence",
    response_model=BenchmarkQuestionRead,
    status_code=status.HTTP_201_CREATED,
)
def add_benchmark_evidence(
    question_id: UUID,
    payload: EvidenceSetCreate,
    session: Annotated[Session, Depends(get_db)],
) -> BenchmarkQuestionRead:
    result = BenchmarkService(session).add_evidence_set(question_id, payload)
    session.commit()
    return result


@router.delete(
    "/benchmark-questions/{question_id}/evidence/{evidence_set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_benchmark_evidence(
    question_id: UUID,
    evidence_set_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> Response:
    BenchmarkService(session).delete_evidence_set(question_id, evidence_set_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/benchmark-questions/{question_id}/leakage-check", response_model=LeakageCheckRead
)
def check_benchmark_question_leakage(
    question_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> LeakageCheckRead:
    return BenchmarkService(session).leakage_check(question_id)


@router.post("/benchmark-versions/{version_id}/freeze", response_model=BenchmarkVersionRead)
def freeze_benchmark_version(
    version_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> BenchmarkVersionRead:
    result = BenchmarkService(session).freeze_version(version_id)
    session.commit()
    return result
