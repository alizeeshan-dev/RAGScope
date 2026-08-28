from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.analysis.service import AnalysisFilters, ExperimentAnalysisService
from backend.app.api.routes.documents import ArtifactRead, get_artifact_store
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Artifact,
    EvaluationResult,
    Experiment,
    ExperimentRun,
    ExperimentStatus,
    Job,
    JobStatus,
    QueryRun,
    QueryRunStatus,
)
from backend.app.db.session import get_db
from backend.app.evaluation.service import EvaluationService
from backend.app.indexing.api_schemas import OperationAccepted
from backend.app.jobs.api_contract import receipt, run_inline_for_bounded_wait
from backend.app.jobs.service import complete_job, create_job, fail_job, request_job_cancellation
from backend.app.query_runtime.schemas import QueryRunCreate
from backend.app.query_runtime.service import QueryOrchestrator

from .contracts import QueryExecutor
from .repository import SQLAlchemyExperimentDependencyResolver, SQLAlchemyExperimentStore
from .schemas import (
    ExperimentCostEstimate,
    ExperimentCreate,
    ExperimentExecutionReport,
    ExperimentProgress,
    ExperimentRecord,
    ExperimentRunCell,
    QueryExecutionOutcome,
)
from .service import ExperimentService, _progress

router = APIRouter(prefix="/experiments", tags=["experiments"])


class ExperimentListPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ExperimentRecord, ...]
    total: int
    offset: int
    limit: int


class ExperimentRunPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ExperimentRunCell, ...]
    total: int
    offset: int
    limit: int


class HumanReviewQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_run_id: UUID
    benchmark_question_id: UUID
    pipeline_configuration_id: UUID
    missing_labels: tuple[str, ...]


class HumanReviewQueuePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[HumanReviewQueueItem, ...]
    total: int
    reviewed: int
    remaining: int
    offset: int
    limit: int


class ExperimentDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment: ExperimentRecord
    progress: ExperimentProgress


def _services(
    session: Session,
) -> tuple[ExperimentService, SQLAlchemyExperimentStore]:
    store = SQLAlchemyExperimentStore(session)
    settings = get_settings()
    return (
        ExperimentService(
            store,
            SQLAlchemyExperimentDependencyResolver(session, settings=settings),
            experiment_cost_limit=settings.experiment_cost_limit,
        ),
        store,
    )


@router.post("", response_model=ExperimentRecord, status_code=status.HTTP_201_CREATED)
def create_experiment(
    payload: ExperimentCreate,
    session: Annotated[Session, Depends(get_db)],
) -> ExperimentRecord:
    service, _store = _services(session)
    return service.create(payload)


@router.get("", response_model=ExperimentListPage)
def list_experiments(
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ExperimentListPage:
    _service, store = _services(session)
    ids = session.scalars(
        select(Experiment.id)
        .order_by(Experiment.created_at.desc(), Experiment.id)
        .offset(offset)
        .limit(limit)
    ).all()
    items = tuple(
        value
        for experiment_id in ids
        if (value := store.get_experiment(experiment_id)) is not None
    )
    total = session.scalar(select(func.count()).select_from(Experiment)) or 0
    return ExperimentListPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/{experiment_id}", response_model=ExperimentDetail)
def get_experiment(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> ExperimentDetail:
    service, store = _services(session)
    del service
    experiment = store.get_experiment(experiment_id)
    if experiment is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    return ExperimentDetail(
        experiment=experiment,
        progress=_progress(store.list_run_cells(experiment_id)),
    )


@router.get("/{experiment_id}/runs", response_model=ExperimentRunPage)
def list_experiment_runs(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ExperimentRunPage:
    _service, store = _services(session)
    if store.get_experiment(experiment_id) is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    all_cells = store.list_run_cells(experiment_id)
    return ExperimentRunPage(
        items=tuple(all_cells[offset : offset + limit]),
        total=len(all_cells),
        offset=offset,
        limit=limit,
    )


@router.get("/{experiment_id}/review-queue", response_model=HumanReviewQueuePage)
def human_review_queue(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HumanReviewQueuePage:
    if session.get(Experiment, experiment_id) is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    required = (
        "answer_correctness",
        "answer_completeness",
        "appropriate_abstention",
        "false_premise_recognition",
        "claim_support_rate",
        "citation_precision",
    )
    runs = list(
        session.scalars(
            select(QueryRun)
            .join(ExperimentRun, ExperimentRun.id == QueryRun.experiment_run_id)
            .where(
                ExperimentRun.experiment_id == experiment_id,
                QueryRun.benchmark_question_id.is_not(None),
            )
            .order_by(QueryRun.benchmark_question_id, QueryRun.pipeline_configuration_id)
        )
    )
    run_ids = [run.id for run in runs]
    labels: dict[UUID, set[str]] = {}
    if run_ids:
        for run_id, name in session.execute(
            select(EvaluationResult.query_run_id, EvaluationResult.metric_name).where(
                EvaluationResult.query_run_id.in_(run_ids),
                EvaluationResult.evaluation_method == "human",
            )
        ):
            labels.setdefault(run_id, set()).add(name)
    items = tuple(
        HumanReviewQueueItem(
            query_run_id=run.id,
            benchmark_question_id=run.benchmark_question_id,
            pipeline_configuration_id=run.pipeline_configuration_id,
            missing_labels=tuple(
                name for name in required if name not in labels.get(run.id, set())
            ),
        )
        for run in runs
        if any(name not in labels.get(run.id, set()) for name in required)
    )
    return HumanReviewQueuePage(
        items=items[offset : offset + limit],
        total=len(runs),
        reviewed=len(runs) - len(items),
        remaining=len(items),
        offset=offset,
        limit=limit,
    )


@router.get("/{experiment_id}/results", response_model=None)
def experiment_results(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    pipeline_configuration_id: Annotated[list[str] | None, Query()] = None,
    question_type: Annotated[list[str] | None, Query()] = None,
    difficulty: Annotated[list[str] | None, Query()] = None,
    run_status: Annotated[list[str] | None, Query()] = None,
    answerability: Annotated[list[str] | None, Query()] = None,
    pipeline_mode: Annotated[list[str] | None, Query()] = None,
    failure_stage: Annotated[list[str] | None, Query()] = None,
    failure_category: Annotated[list[str] | None, Query()] = None,
    failure_code: Annotated[list[str] | None, Query()] = None,
    include_infrastructure_failures: bool = True,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, object]:
    result = ExperimentAnalysisService(session).results(
        experiment_id,
        filters=AnalysisFilters(
            pipeline_ids=tuple(pipeline_configuration_id or ()),
            question_types=tuple(question_type or ()),
            difficulties=tuple(difficulty or ()),
            run_statuses=tuple(run_status or ()),
            answerabilities=tuple(answerability or ()),
            pipeline_modes=tuple(pipeline_mode or ()),
            failure_stages=tuple(failure_stage or ()),
            failure_categories=tuple(failure_category or ()),
            failure_codes=tuple(failure_code or ()),
            include_infrastructure_failures=include_infrastructure_failures,
        ),
        offset=offset,
        limit=limit,
    )
    return result.json_value()


@router.get("/{experiment_id}/export", response_model=None)
def export_experiment(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    format: Literal["csv", "json"] = "json",
    kind: Literal["raw", "aggregate"] = "raw",
) -> Response:
    artifact_type = (
        "experiment-analysis.json"
        if format == "json"
        else (
            "experiment-aggregates.csv"
            if kind == "aggregate"
            else "experiment-runs.csv"
        )
    )
    artifact = session.scalar(
        select(Artifact)
        .where(
            Artifact.experiment_id == experiment_id,
            Artifact.artifact_type == artifact_type,
        )
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
    )
    if artifact is None:
        raise DomainError(
            "EXPERIMENT_EXPORT_NOT_READY",
            "Generate the immutable experiment export before downloading it.",
            status_code=409,
        )
    content = artifact_store.read_bytes(artifact.storage_key)
    media_type = artifact.media_type
    suffix = artifact.original_filename or artifact_type
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="experiment-{experiment_id}-{suffix}"'
            )
        },
    )


@router.post(
    "/{experiment_id}/exports",
    response_model=OperationAccepted,
    status_code=202,
)
def generate_experiment_exports(
    experiment_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> OperationAccepted:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    job, _created = create_job(
        session,
        job_type="experiment_export",
        input_reference={"experiment_id": str(experiment_id), "schema_version": "v1"},
        idempotency_key=(
            f"experiment-export:{experiment_id}:"
            f"{experiment.configuration_hash or 'draft'}:{experiment.completed_at}"
        ),
        progress_total=3,
    )
    session.commit()
    if settings.job_api_default_wait if wait is None else wait:
        completed = run_inline_for_bounded_wait(
            session,
            job,
            settings=settings,
            artifact_store=artifact_store,
            timeout_seconds=timeout_seconds,
        )
        response.status_code = 200 if completed else 202
    return receipt(job, resource_type="experiment", resource_id=experiment_id)


@router.get("/{experiment_id}/exports", response_model=list[ArtifactRead])
def list_experiment_exports(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> list[Artifact]:
    if session.get(Experiment, experiment_id) is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    return list(
        session.scalars(
            select(Artifact)
            .where(Artifact.experiment_id == experiment_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .limit(200)
        )
    )


@router.post("/{experiment_id}/pause", response_model=ExperimentRecord)
def pause_experiment(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> ExperimentRecord:
    _service, store = _services(session)
    experiment = store.get_experiment(experiment_id)
    if experiment is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    if experiment.status.value not in {"running", "paused"}:
        raise DomainError(
            "INVALID_EXPERIMENT_TRANSITION",
            "Only a running experiment can be paused.",
        )
    row = session.get(Experiment, experiment_id)
    if row is not None and row.current_job_id is not None:
        job = session.get(Job, row.current_job_id)
        if job is not None and job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            request_job_cancellation(job)
    paused = experiment.model_copy(update={"status": ExperimentStatus.PAUSED})
    store.save_experiment(paused)
    return paused


@router.post("/{experiment_id}/freeze", response_model=ExperimentRecord)
def freeze_experiment(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
) -> ExperimentRecord:
    service, _store = _services(session)
    experiment = service.freeze(experiment_id)
    payload = experiment.model_dump_json().encode("utf-8")
    descriptor = artifact_store.put_bytes(
        payload,
        media_type="application/json",
        original_filename=f"experiment-{experiment.id}-frozen.json",
        producing_operation="experiment-freeze",
        configuration={"configuration_hash": experiment.configuration_hash or ""},
    )
    session.add(
        Artifact(
            id=descriptor.id,
            experiment_id=experiment.id,
            artifact_type="frozen-experiment-configuration.json",
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=descriptor.original_filename,
            producing_operation=descriptor.producing_operation,
            producer_version="experiment-snapshot-v1",
            configuration=descriptor.configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )
    )
    session.commit()
    return experiment


@router.post("/{experiment_id}/estimate", response_model=ExperimentCostEstimate)
def estimate_experiment(
    experiment_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> ExperimentCostEstimate:
    service, _store = _services(session)
    return service.estimate(experiment_id)


@router.post(
    "/{experiment_id}/start",
    response_model=ExperimentExecutionReport | OperationAccepted,
)
def start_experiment(
    experiment_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> ExperimentExecutionReport | OperationAccepted:
    service, store = _services(session)
    experiment = store.get_experiment(experiment_id)
    if experiment is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    job = _enqueue_operation_job(
        session,
        experiment_id=experiment_id,
        operation="start",
        operation_key=experiment.configuration_hash or "draft",
        progress_total=len(store.list_run_cells(experiment_id)),
    )
    if not (settings.job_api_default_wait if wait is None else wait):
        response.status_code = 202
        return receipt(job, resource_type="experiment", resource_id=experiment_id)
    completed_job = run_inline_for_bounded_wait(
        session,
        job,
        settings=settings,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
    )
    if not completed_job:
        response.status_code = 202
        return receipt(job, resource_type="experiment", resource_id=experiment_id)
    completed = store.get_experiment(experiment_id)
    assert completed is not None
    return ExperimentExecutionReport(
        experiment=completed,
        progress=_progress(store.list_run_cells(experiment_id)),
        executed_attempts=sum(
            value.attempt_count for value in store.list_run_cells(experiment_id)
        ),
    )


@router.post(
    "/{experiment_id}/resume",
    response_model=ExperimentExecutionReport | OperationAccepted,
)
def resume_experiment(
    experiment_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> ExperimentExecutionReport | OperationAccepted:
    service, store = _services(session)
    experiment = store.get_experiment(experiment_id)
    if experiment is None:
        raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
    attempt_total = sum(value.attempt_count for value in store.list_run_cells(experiment_id))
    job = _enqueue_operation_job(
        session,
        experiment_id=experiment_id,
        operation="resume",
        operation_key=str(attempt_total),
        progress_total=len(store.list_run_cells(experiment_id)),
    )
    if not (settings.job_api_default_wait if wait is None else wait):
        response.status_code = 202
        return receipt(job, resource_type="experiment", resource_id=experiment_id)
    completed_job = run_inline_for_bounded_wait(
        session,
        job,
        settings=settings,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
    )
    if not completed_job:
        response.status_code = 202
        return receipt(job, resource_type="experiment", resource_id=experiment_id)
    completed = store.get_experiment(experiment_id)
    assert completed is not None
    return ExperimentExecutionReport(
        experiment=completed,
        progress=_progress(store.list_run_cells(experiment_id)),
        executed_attempts=sum(
            value.attempt_count for value in store.list_run_cells(experiment_id)
        ),
    )


def _query_executor(session: Session) -> QueryExecutor:
    orchestrator = QueryOrchestrator(session)

    def execute(
        cell: ExperimentRunCell, request: QueryRunCreate
    ) -> QueryExecutionOutcome:
        run = orchestrator.execute(
            request,
            raise_on_failure=False,
            experiment_run_id=cell.id,
            experiment_attempt_number=cell.attempt_count,
        )
        try:
            EvaluationService(session).evaluate(run.id)
            session.commit()
        except Exception:  # noqa: BLE001 - persisted as a non-retryable research defect
            session.rollback()
            return QueryExecutionOutcome(
                query_run_id=run.id,
                succeeded=False,
                failure_code="EXPERIMENT_EVALUATION_FAILURE",
                failure_message="The completed run could not be evaluated.",
            )
        return QueryExecutionOutcome(
            query_run_id=run.id,
            succeeded=run.status is QueryRunStatus.SUCCEEDED,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
        )

    return execute


def _enqueue_operation_job(
    session: Session,
    *,
    experiment_id: UUID,
    operation: str,
    operation_key: str,
    progress_total: int,
) -> Job:
    job, created = create_job(
        session,
        job_type="experiment-execution",
        input_reference={"experiment_id": str(experiment_id), "operation": operation},
        idempotency_key=f"experiment:{experiment_id}:{operation}:{operation_key}",
        progress_total=progress_total,
    )
    if not created and job.status is JobStatus.SUCCEEDED:
        raise DomainError(
            "EXPERIMENT_OPERATION_ALREADY_COMPLETED",
            "This exact experiment operation already completed.",
        )
    experiment = session.get(Experiment, experiment_id)
    if experiment is not None:
        experiment.current_job_id = job.id
    session.commit()
    return job


def _complete_operation_job(session: Session, job: Job) -> None:
    complete_job(job)
    session.commit()


def _fail_operation_job(session: Session, job: Job, error: DomainError) -> None:
    session.refresh(job)
    if job.status is JobStatus.RUNNING:
        fail_job(job, error_code=error.code, error_message=error.message)
        session.commit()
