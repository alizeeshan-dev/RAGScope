from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.session import get_db

from .schemas import (
    CitationVerificationOverrideCreate,
    CitationVerificationRead,
    EvaluationBundleRead,
    EvaluationTrigger,
    FailureAttributionRead,
    FailureOverrideCreate,
    HumanMetricCreate,
)
from .service import EvaluationService

router = APIRouter(tags=["evaluation"])


@router.get("/query-runs/{run_id}/evaluation", response_model=EvaluationBundleRead)
def get_evaluation(
    run_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> EvaluationBundleRead:
    service = EvaluationService(session)
    try:
        run = service.get_run(run_id)
    except LookupError as exc:
        raise DomainError("QUERY_RUN_NOT_FOUND", str(exc), status_code=404) from exc
    return EvaluationBundleRead(
        query_run_id=run.id,
        benchmark_question_id=run.benchmark_question_id,
        metrics=service.list_results(run.id),
        failure_attributions=service.list_attributions(run.id),
    )


@router.post("/query-runs/{run_id}/evaluation", response_model=EvaluationBundleRead)
def evaluate_run(
    run_id: UUID,
    payload: EvaluationTrigger,
    session: Annotated[Session, Depends(get_db)],
) -> EvaluationBundleRead:
    service = EvaluationService(session)
    try:
        run = service.get_run(run_id)
        service.evaluate(
            run.id,
            metric_versions=set(payload.metric_versions) if payload.metric_versions else None,
        )
    except LookupError as exc:
        raise DomainError("QUERY_RUN_NOT_FOUND", str(exc), status_code=404) from exc
    session.commit()
    return EvaluationBundleRead(
        query_run_id=run.id,
        benchmark_question_id=run.benchmark_question_id,
        metrics=service.list_results(run.id),
        failure_attributions=service.list_attributions(run.id),
    )


@router.post(
    "/query-runs/{run_id}/evaluation/human-labels",
    response_model=EvaluationBundleRead,
)
def add_human_evaluation(
    run_id: UUID,
    payload: HumanMetricCreate,
    session: Annotated[Session, Depends(get_db)],
) -> EvaluationBundleRead:
    service = EvaluationService(session)
    try:
        run = service.get_run(run_id)
        service.add_human_metric(run.id, payload)
    except LookupError as exc:
        raise DomainError("QUERY_RUN_NOT_FOUND", str(exc), status_code=404) from exc
    session.commit()
    return EvaluationBundleRead(
        query_run_id=run.id,
        benchmark_question_id=run.benchmark_question_id,
        metrics=service.list_results(run.id),
        failure_attributions=service.list_attributions(run.id),
    )


@router.post(
    "/query-runs/{run_id}/failure-attributions/{attribution_id}/review",
    response_model=FailureAttributionRead,
)
def review_failure_attribution(
    run_id: UUID,
    attribution_id: UUID,
    payload: FailureOverrideCreate,
    session: Annotated[Session, Depends(get_db)],
) -> object:
    service = EvaluationService(session)
    try:
        row = service.override_failure(
            run_id,
            attribution_id,
            label=payload.label,
            note=payload.reviewer_note,
        )
    except LookupError as exc:
        raise DomainError("FAILURE_ATTRIBUTION_NOT_FOUND", str(exc), status_code=404) from exc
    except ValueError as exc:
        raise DomainError("INVALID_FAILURE_LABEL", str(exc), status_code=422) from exc
    session.commit()
    session.refresh(row)
    return row


@router.post(
    "/citations/{citation_id}/verification/review",
    response_model=CitationVerificationRead,
)
def review_citation_verification(
    citation_id: UUID,
    payload: CitationVerificationOverrideCreate,
    session: Annotated[Session, Depends(get_db)],
) -> object:
    try:
        row = EvaluationService(session).override_citation(
            citation_id,
            label=payload.label,
            score=payload.score,
            note=payload.reviewer_note,
        )
    except LookupError as exc:
        raise DomainError("CITATION_VERIFICATION_NOT_FOUND", str(exc), status_code=404) from exc
    session.commit()
    session.refresh(row)
    return row
