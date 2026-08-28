from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.app.api.routes.documents import get_artifact_store
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.db.session import get_db
from backend.app.indexing.api_schemas import OperationAccepted
from backend.app.jobs.api_contract import receipt, run_inline_for_bounded_wait
from backend.app.jobs.service import create_job

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


@router.post(
    "/query-runs/{run_id}/evaluation",
    response_model=EvaluationBundleRead | OperationAccepted,
)
def evaluate_run(
    run_id: UUID,
    payload: EvaluationTrigger,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> EvaluationBundleRead | OperationAccepted:
    service = EvaluationService(session)
    try:
        run = service.get_run(run_id)
        job, _created = create_job(
            session,
            job_type="query_evaluation",
            input_reference={
                "query_run_id": str(run.id),
            "metric_versions": list(payload.metric_versions or []),
            },
            idempotency_key=(
                f"evaluation:{run.id}:" + ",".join(sorted(payload.metric_versions or []))
            ),
            progress_total=1,
        )
    except LookupError as exc:
        raise DomainError("QUERY_RUN_NOT_FOUND", str(exc), status_code=404) from exc
    session.commit()
    if not (settings.job_api_default_wait if wait is None else wait):
        response.status_code = 202
        return receipt(job, resource_type="query_run", resource_id=run.id)
    completed = run_inline_for_bounded_wait(
        session,
        job,
        settings=settings,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
    )
    if not completed:
        response.status_code = 202
        return receipt(job, resource_type="query_run", resource_id=run.id)
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
