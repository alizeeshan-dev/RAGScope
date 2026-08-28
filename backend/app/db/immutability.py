from typing import Any, cast

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Artifact,
    BenchmarkEvidenceReference,
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    BenchmarkVersion,
    BenchmarkVersionStatus,
    Citation,
    CitationVerification,
    ContextSource,
    CorpusVersion,
    CorpusVersionStatus,
    EvaluationResult,
    Experiment,
    ExperimentRun,
    ExperimentStatus,
    FailureAttribution,
    GeneratedClaim,
    PipelineConfiguration,
    PromptTemplate,
    QueryRun,
    RetrievalResultRecord,
    RouterConfiguration,
    TraceSpan,
)


@event.listens_for(Session, "before_flush")
def protect_version_configuration(
    session: Session, _flush_context: object, _instances: object
) -> None:
    """Provide an ORM-level backstop in addition to service/API state checks."""

    configuration_fields = (
        "parser_configuration",
        "chunker_configuration",
        "embedding_configuration",
    )
    for obj in session.dirty:
        if isinstance(obj, Experiment):
            state = cast(Any, inspect(obj))
            freezing_now = (
                obj.frozen_at is not None and state.attrs.frozen_at.history.has_changes()
            )
            immutable_fields = (
                "name",
                "research_question",
                "corpus_version_id",
                "benchmark_version_id",
                "pipeline_configuration_ids",
                "repetitions",
                "code_commit",
                "stop_on_error",
                "retry_policy",
                "dependency_snapshot",
                "configuration_hash",
                "frozen_at",
            )
            if obj.frozen_at is not None and not freezing_now and any(
                state.attrs[field].history.has_changes() for field in immutable_fields
            ):
                raise DomainError(
                    "EXPERIMENT_VERSION_CONFLICT",
                    "A frozen experiment's research configuration cannot be modified.",
                    status_code=409,
                )
        if isinstance(obj, (PipelineConfiguration, PromptTemplate, RouterConfiguration)):
            state = cast(Any, inspect(obj))
            frozen_history = state.attrs.frozen_at.history
            freezing_now = obj.frozen_at is not None and frozen_history.has_changes()
            if obj.frozen_at is not None and not freezing_now:
                raise DomainError(
                    (
                        "PIPELINE_CONFIGURATION_IMMUTABLE"
                        if isinstance(obj, PipelineConfiguration)
                        else (
                            "ROUTER_CONFIGURATION_IMMUTABLE"
                            if isinstance(obj, RouterConfiguration)
                            else "PROMPT_IMMUTABLE"
                        )
                    ),
                    "A frozen configuration snapshot cannot be modified.",
                )
        if not isinstance(obj, CorpusVersion):
            continue
        state = inspect(obj)
        frozen_history = state.attrs.frozen_at.history
        freezing_now = obj.frozen_at is not None and frozen_history.has_changes()
        if obj.frozen_at is not None and not freezing_now:
            raise DomainError(
                "CORPUS_VERSION_IMMUTABLE", "A frozen corpus version cannot be modified."
            )
        if obj.status is not CorpusVersionStatus.DRAFT and any(
            state.attrs[field].history.has_changes() for field in configuration_fields
        ):
            raise DomainError(
                "CORPUS_VERSION_IMMUTABLE",
                "Processing configuration can only change while the version is a draft.",
            )

    for obj in {*session.new, *session.dirty, *session.deleted}:
        version = _benchmark_version(obj, session)
        if version is None or version.status is not BenchmarkVersionStatus.FROZEN:
            continue
        if isinstance(obj, BenchmarkVersion) and obj in session.dirty:
            state = cast(Any, inspect(obj))
            freezing_now = (
                state.attrs.status.history.has_changes()
                and obj.status is BenchmarkVersionStatus.FROZEN
            )
            if freezing_now:
                continue
        raise DomainError(
            "BENCHMARK_VERSION_IMMUTABLE",
            "A frozen benchmark version and its annotations cannot be modified.",
            status_code=409,
        )

    protected_results = (
        QueryRun,
        RetrievalResultRecord,
        ContextSource,
        TraceSpan,
        GeneratedClaim,
        Citation,
        EvaluationResult,
        CitationVerification,
        FailureAttribution,
        Artifact,
    )
    for obj in {*session.new, *session.dirty, *session.deleted}:
        if not isinstance(obj, protected_results):
            continue
        experiment = _result_experiment(obj, session)
        if experiment is not None and experiment.status in {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.COMPLETED_WITH_FAILURES,
        }:
            if _allowed_completed_experiment_analysis_change(obj, session):
                continue
            raise DomainError(
                "EXPERIMENT_RAW_RESULTS_IMMUTABLE",
                "Completed experiment raw results cannot be added, changed, or deleted.",
                status_code=409,
            )


def _allowed_completed_experiment_analysis_change(obj: object, session: Session) -> bool:
    """Allow append-only derived analysis and narrowly scoped human review.

    Query execution evidence stays immutable. Evaluation/failure/verifier rows are
    derived from that evidence and may gain a new version after completion. Existing
    automatic judgments can only gain human override fields; they cannot be rewritten.
    """

    if obj in session.new:
        return isinstance(obj, (EvaluationResult, CitationVerification, FailureAttribution))
    if obj in session.deleted:
        return False
    allowed_fields: set[str]
    if isinstance(obj, CitationVerification):
        allowed_fields = {"human_label", "human_score", "human_note", "human_reviewed_at"}
    elif isinstance(obj, FailureAttribution):
        allowed_fields = {
            "human_override_label",
            "human_override_note",
            "human_reviewed_at",
        }
    else:
        return False
    state = cast(Any, inspect(obj))
    changed_fields = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }
    return bool(changed_fields) and changed_fields <= allowed_fields


def _benchmark_version(obj: object, session: Session) -> BenchmarkVersion | None:
    if isinstance(obj, BenchmarkVersion):
        return obj
    if isinstance(obj, BenchmarkQuestion):
        return obj.benchmark_version or session.get(
            BenchmarkVersion, obj.benchmark_version_id
        )
    if isinstance(obj, BenchmarkEvidenceSet):
        question = obj.benchmark_question or session.get(
            BenchmarkQuestion, obj.benchmark_question_id
        )
        return _benchmark_version(question, session) if question is not None else None
    if isinstance(obj, BenchmarkEvidenceReference):
        evidence_set = obj.evidence_set or session.get(
            BenchmarkEvidenceSet, obj.evidence_set_id
        )
        return (
            _benchmark_version(evidence_set, session)
            if evidence_set is not None
            else None
        )
    return None


def _result_experiment(obj: object, session: Session) -> Experiment | None:
    run: QueryRun | None = None
    if isinstance(obj, QueryRun):
        run = obj
    elif isinstance(obj, Artifact) and obj.query_run_id is not None:
        run = session.get(QueryRun, obj.query_run_id)
    elif isinstance(
        obj,
        (
            RetrievalResultRecord,
            ContextSource,
            TraceSpan,
            GeneratedClaim,
            Citation,
            EvaluationResult,
            FailureAttribution,
        ),
    ):
        run = session.get(QueryRun, obj.query_run_id)
    elif isinstance(obj, CitationVerification):
        citation = session.get(Citation, obj.citation_id)
        run = session.get(QueryRun, citation.query_run_id) if citation is not None else None
    if run is None or run.experiment_run_id is None:
        return None
    cell = session.get(ExperimentRun, run.experiment_run_id)
    return session.get(Experiment, cell.experiment_id) if cell is not None else None
