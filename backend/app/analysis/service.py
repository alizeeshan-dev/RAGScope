"""Database projection for reproducible experiment aggregation and export."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.adaptive.comparison import compare_adaptive_to_fixed
from backend.app.adaptive.schemas import BestObservedCriteria, RunObservation
from backend.app.analysis.aggregation import (
    aggregate_evidence_survival,
    aggregate_metrics,
)
from backend.app.analysis.contracts import (
    EVIDENCE_SURVIVAL_METRIC_VERSION,
    AggregateMetric,
    AggregationPlan,
    AnalysisExportBundle,
    AnalysisRun,
    DenominatorPolicy,
    EvidenceSurvivalObservation,
    FilterCondition,
    MetricObservation,
    MetricSelector,
)
from backend.app.analysis.exports import build_export_bundle
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Artifact,
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    Chunk,
    ContextSource,
    EvaluationResult,
    Experiment,
    ExperimentRun,
    ExperimentRunAttempt,
    FailureAttribution,
    PipelineConfiguration,
    QueryRun,
    RetrievalResultRecord,
    TraceSpan,
)
from backend.app.experiments.schemas import ExperimentAnalysisConfiguration
from backend.app.tracing.redaction import configured_sensitive_values, redact

INFRASTRUCTURE_FAILURE_CODES = frozenset(
    {
        "MODEL_PROVIDER_FAILURE",
        "EMBEDDING_PROVIDER_FAILURE",
        "DATABASE_FAILURE",
        "TIMEOUT",
        "INVALID_STRUCTURED_OUTPUT",
        "EXPERIMENT_RUNNER_FAILURE",
        "EXPERIMENT_INTERRUPTED",
        "EXPERIMENT_EVALUATION_FAILURE",
    }
)


@dataclass(frozen=True, slots=True)
class AnalysisFilters:
    pipeline_ids: tuple[str, ...] = ()
    question_types: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()
    run_statuses: tuple[str, ...] = ()
    answerabilities: tuple[str, ...] = ()
    pipeline_modes: tuple[str, ...] = ()
    failure_stages: tuple[str, ...] = ()
    failure_categories: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    include_infrastructure_failures: bool = True


DEFAULT_ANALYSIS_FILTERS = AnalysisFilters()


@dataclass(frozen=True, slots=True)
class ExperimentAnalysisResult:
    experiment_id: str
    filters: dict[str, Any]
    sample_size: int
    infrastructure_failures: int
    aggregates: tuple[AggregateMetric, ...]
    evidence_survival: tuple[AggregateMetric, ...]
    adaptive_analysis: dict[str, Any] | None
    headline_metrics: dict[str, dict[str, Any]]
    visualizations: dict[str, Any]
    available_dimensions: dict[str, tuple[str, ...]]
    runs: tuple[AnalysisRun, ...]
    total_runs: int
    offset: int
    limit: int

    def json_value(self) -> dict[str, Any]:
        return asdict(self)


class ExperimentAnalysisService:
    """Project immutable terminal QueryRuns into the pure analysis layer."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def results(
        self,
        experiment_id: UUID,
        *,
        filters: AnalysisFilters = DEFAULT_ANALYSIS_FILTERS,
        offset: int = 0,
        limit: int = 500,
    ) -> ExperimentAnalysisResult:
        experiment = self.session.get(Experiment, experiment_id)
        if experiment is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        all_runs = self._project(experiment)
        filtered = tuple(run for run in all_runs if _matches(run, filters))
        selectors = _selectors(filtered)
        aggregate_rows: tuple[AggregateMetric, ...] = ()
        survival_rows: tuple[AggregateMetric, ...] = ()
        if selectors:
            conditions = _filter_conditions(filters)
            aggregate_rows = aggregate_metrics(
                all_runs,
                AggregationPlan(
                    selectors=selectors,
                    group_by=("pipeline_id", "pipeline_name"),
                    filters=conditions,
                ),
            ).aggregates
            survival_rows = aggregate_evidence_survival(
                all_runs,
                metric_version=EVIDENCE_SURVIVAL_METRIC_VERSION,
                denominator_policy=_quality_policy(),
                group_by=("pipeline_id", "pipeline_name"),
                filters=conditions,
            ).aggregates
        page = filtered[offset : offset + limit]
        return ExperimentAnalysisResult(
            experiment_id=str(experiment.id),
            filters=asdict(filters),
            sample_size=len(filtered),
            infrastructure_failures=sum(run.has_infrastructure_failure for run in filtered),
            aggregates=aggregate_rows,
            evidence_survival=survival_rows,
            adaptive_analysis=_adaptive_analysis(experiment, filtered),
            headline_metrics=_headline_metrics(filtered),
            visualizations=_visualization_data(filtered),
            available_dimensions=_available_dimensions(all_runs),
            runs=page,
            total_runs=len(filtered),
            offset=offset,
            limit=limit,
        )

    def all_runs(self, experiment_id: UUID) -> tuple[AnalysisRun, ...]:
        experiment = self.session.get(Experiment, experiment_id)
        if experiment is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        return self._project(experiment)

    def export_bundle(
        self,
        experiment_id: UUID,
        *,
        filters: AnalysisFilters = DEFAULT_ANALYSIS_FILTERS,
    ) -> AnalysisExportBundle:
        all_runs = self.all_runs(experiment_id)
        filtered = tuple(run for run in all_runs if _matches(run, filters))
        plan = AggregationPlan(
            selectors=_selectors(filtered),
            group_by=("pipeline_id", "pipeline_name"),
        )
        return build_export_bundle(filtered, aggregate_metrics(filtered, plan))

    def research_audit_manifest(self, experiment_id: UUID) -> dict[str, Any]:
        """Return deterministic research identity and raw-result references.

        Exact large payloads remain in immutable artifacts. The manifest deliberately
        exports hashes and database identifiers, never local artifact storage paths.
        """

        experiment = self.session.get(Experiment, experiment_id)
        if experiment is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        cells = list(
            self.session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment_id)
                .order_by(
                    ExperimentRun.benchmark_question_id,
                    ExperimentRun.pipeline_configuration_id,
                    ExperimentRun.repetition_index,
                )
            )
        )
        cell_ids = [cell.id for cell in cells]
        attempts = (
            list(
                self.session.scalars(
                    select(ExperimentRunAttempt)
                    .where(ExperimentRunAttempt.experiment_run_id.in_(cell_ids))
                    .order_by(
                        ExperimentRunAttempt.experiment_run_id,
                        ExperimentRunAttempt.attempt_number,
                    )
                )
            )
            if cell_ids
            else []
        )
        query_runs = (
            list(
                self.session.scalars(
                    select(QueryRun)
                    .where(QueryRun.experiment_run_id.in_(cell_ids))
                    .order_by(QueryRun.experiment_run_id, QueryRun.experiment_attempt_number)
                )
            )
            if cell_ids
            else []
        )
        query_run_ids = [run.id for run in query_runs]
        artifacts = (
            list(
                self.session.scalars(
                    select(Artifact)
                    .where(Artifact.query_run_id.in_(query_run_ids))
                    .order_by(Artifact.query_run_id, Artifact.artifact_type, Artifact.id)
                )
            )
            if query_run_ids
            else []
        )
        span_counts: dict[UUID, int] = defaultdict(int)
        if query_run_ids:
            for run_id, count in self.session.execute(
                select(TraceSpan.query_run_id, func.count())
                .where(TraceSpan.query_run_id.in_(query_run_ids))
                .group_by(TraceSpan.query_run_id)
            ):
                span_counts[run_id] = int(count)

        safe_snapshot = redact(
            experiment.dependency_snapshot,
            sensitive_values=configured_sensitive_values(),
        )
        safe_cost = redact(
            experiment.cost_estimate,
            sensitive_values=configured_sensitive_values(),
        )
        return {
            "manifest_version": "ragscope-research-audit.v1",
            "experiment": {
                "id": str(experiment.id),
                "name": experiment.name,
                "research_question": experiment.research_question,
                "status": experiment.status.value,
                "corpus_version_id": str(experiment.corpus_version_id),
                "benchmark_version_id": str(experiment.benchmark_version_id),
                "pipeline_configuration_ids": experiment.pipeline_configuration_ids,
                "repetitions": experiment.repetitions,
                "code_commit": experiment.code_commit,
                "configuration_hash": experiment.configuration_hash,
                "dependency_snapshot": safe_snapshot,
                "cost_estimate": safe_cost,
                "frozen_at": _iso(experiment.frozen_at),
                "started_at": _iso(experiment.started_at),
                "completed_at": _iso(experiment.completed_at),
            },
            "run_matrix": [
                {
                    "id": str(cell.id),
                    "benchmark_question_id": str(cell.benchmark_question_id),
                    "pipeline_configuration_id": str(cell.pipeline_configuration_id),
                    "repetition_index": cell.repetition_index,
                    "idempotency_key": cell.idempotency_key,
                    "random_seed": cell.random_seed,
                    "status": cell.status.value,
                    "attempt_count": cell.attempt_count,
                    "max_attempts": cell.max_attempts,
                    "last_failure_code": cell.last_failure_code,
                }
                for cell in cells
            ],
            "attempts": [
                {
                    "id": str(attempt.id),
                    "experiment_run_id": str(attempt.experiment_run_id),
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status.value,
                    "query_run_id": (
                        str(attempt.query_run_id) if attempt.query_run_id else None
                    ),
                    "failure_code": attempt.failure_code,
                    "started_at": _iso(attempt.started_at),
                    "finished_at": _iso(attempt.finished_at),
                }
                for attempt in attempts
            ],
            "query_runs": [
                {
                    "id": str(run.id),
                    "experiment_run_id": str(run.experiment_run_id),
                    "experiment_attempt_number": run.experiment_attempt_number,
                    "status": run.status.value,
                    "failure_code": run.failure_code,
                    "answerability": (
                        run.answerability_decision.value
                        if run.answerability_decision is not None
                        else None
                    ),
                    "total_latency_ms": run.total_latency_ms,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "estimated_cost": run.estimated_cost,
                    "context_artifact_id": (
                        str(run.context_artifact_id) if run.context_artifact_id else None
                    ),
                    "raw_response_artifact_id": (
                        str(run.raw_response_artifact_id)
                        if run.raw_response_artifact_id
                        else None
                    ),
                    "trace_span_count": span_counts[run.id],
                }
                for run in query_runs
            ],
            "artifacts": [
                {
                    "id": str(artifact.id),
                    "query_run_id": str(artifact.query_run_id),
                    "trace_span_id": (
                        str(artifact.trace_span_id) if artifact.trace_span_id else None
                    ),
                    "artifact_type": artifact.artifact_type,
                    "content_hash": artifact.content_hash,
                    "media_type": artifact.media_type,
                    "producing_operation": artifact.producing_operation,
                    "producer_version": artifact.producer_version,
                    "size_bytes": artifact.size_bytes,
                }
                for artifact in artifacts
            ],
        }

    def _project(self, experiment: Experiment) -> tuple[AnalysisRun, ...]:
        cells = list(
            self.session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment.id)
                .order_by(
                    ExperimentRun.benchmark_question_id,
                    ExperimentRun.pipeline_configuration_id,
                    ExperimentRun.repetition_index,
                )
            )
        )
        if not cells:
            return ()
        cell_ids = [cell.id for cell in cells]
        query_rows = list(
            self.session.scalars(
                select(QueryRun).where(QueryRun.experiment_run_id.in_(cell_ids))
            )
        )
        latest_by_cell: dict[UUID, QueryRun] = {}
        for run in query_rows:
            if run.experiment_run_id is None:
                continue
            previous = latest_by_cell.get(run.experiment_run_id)
            if previous is None or (run.experiment_attempt_number or 0) > (
                previous.experiment_attempt_number or 0
            ):
                latest_by_cell[run.experiment_run_id] = run
        selected_runs = list(latest_by_cell.values())
        if not selected_runs:
            return ()
        run_ids = [run.id for run in selected_runs]
        question_ids = {
            run.benchmark_question_id
            for run in selected_runs
            if run.benchmark_question_id is not None
        }
        pipeline_ids = {run.pipeline_configuration_id for run in selected_runs}
        questions = {
            row.id: row
            for row in self.session.scalars(
                select(BenchmarkQuestion)
                .where(BenchmarkQuestion.id.in_(question_ids))
                .options(
                    selectinload(BenchmarkQuestion.evidence_sets).selectinload(
                        BenchmarkEvidenceSet.references
                    )
                )
            )
        }
        pipelines = {
            row.id: row
            for row in self.session.scalars(
                select(PipelineConfiguration).where(PipelineConfiguration.id.in_(pipeline_ids))
            )
        }
        metric_rows: dict[
            UUID, dict[tuple[str, str, str, str], EvaluationResult]
        ] = defaultdict(dict)
        for metric_row in self.session.scalars(
            select(EvaluationResult)
            .where(EvaluationResult.query_run_id.in_(run_ids))
            .order_by(EvaluationResult.created_at, EvaluationResult.id)
        ):
            identity = (
                metric_row.metric_name,
                metric_row.metric_version,
                metric_row.metric_scope.value,
                metric_row.evaluation_method,
            )
            metric_rows[metric_row.query_run_id][identity] = metric_row
        attributions: dict[UUID, list[FailureAttribution]] = defaultdict(list)
        for attribution_row in self.session.scalars(
            select(FailureAttribution).where(FailureAttribution.query_run_id.in_(run_ids))
        ):
            attributions[attribution_row.query_run_id].append(attribution_row)
        retrieval: dict[UUID, list[RetrievalResultRecord]] = defaultdict(list)
        for retrieval_row in self.session.scalars(
            select(RetrievalResultRecord).where(RetrievalResultRecord.query_run_id.in_(run_ids))
        ):
            retrieval[retrieval_row.query_run_id].append(retrieval_row)
        context: dict[UUID, list[ContextSource]] = defaultdict(list)
        for context_row in self.session.scalars(
            select(ContextSource).where(ContextSource.query_run_id.in_(run_ids))
        ):
            context[context_row.query_run_id].append(context_row)
        element_chunks = _element_chunks(self.session, experiment.corpus_version_id)

        cell_by_id = {cell.id: cell for cell in cells}
        projected: list[AnalysisRun] = []
        for run in sorted(selected_runs, key=lambda value: str(value.id)):
            cell = cell_by_id[run.experiment_run_id]  # type: ignore[index]
            question = (
                questions.get(run.benchmark_question_id)
                if run.benchmark_question_id is not None
                else None
            )
            pipeline = pipelines[run.pipeline_configuration_id]
            primary = next(
                (item for item in attributions[run.id] if item.is_primary),
                None,
            )
            infrastructure_code = (
                run.failure_code if run.failure_code in INFRASTRUCTURE_FAILURE_CODES else None
            )
            projected.append(
                AnalysisRun(
                    run_id=str(run.id),
                    pipeline_id=str(run.pipeline_configuration_id),
                    question_id=str(run.benchmark_question_id),
                    run_status=run.status.value,
                    infrastructure_failure_code=infrastructure_code,
                    dimensions={
                        "pipeline_name": pipeline.name,
                        "question_type": question.question_type.value if question else None,
                        "difficulty": question.difficulty.value if question else None,
                        "answerability": (
                            question.expected_answerability.value if question else None
                        ),
                        "pipeline_mode": pipeline.execution_mode.value,
                        "chosen_route": run.route_decision.get("retrieval_mode"),
                        "route_identifier": _route_identifier(run),
                        "repetition": cell.repetition_index,
                        "failure_stage": (
                            primary.pipeline_stage
                            if primary is not None
                            else ("infrastructure" if infrastructure_code else None)
                        ),
                        "failure_category": (
                            primary.human_override_label or primary.automatic_label
                            if primary is not None
                            else infrastructure_code
                        ),
                        "total_latency_ms": run.total_latency_ms,
                        "input_tokens": run.input_tokens,
                        "output_tokens": run.output_tokens,
                        "estimated_cost": run.estimated_cost,
                        "cost_currency": experiment.estimated_cost_currency,
                    },
                    metrics=tuple(
                        MetricObservation(
                            name=row.metric_name,
                            version=row.metric_version,
                            scope=row.metric_scope.value,
                            method=row.evaluation_method,
                            value=row.metric_value,
                            details=_redacted_dict(row.details),
                        )
                        for row in sorted(
                            metric_rows[run.id].values(),
                            key=lambda item: (
                                item.metric_name,
                                item.metric_version,
                                item.evaluation_method,
                            ),
                        )
                    ),
                    evidence_survival=_survival(
                        question,
                        retrieval[run.id],
                        context[run.id],
                        element_chunks,
                    ),
                )
            )
        return tuple(projected)


_HEADLINE_ALIASES: dict[str, tuple[str, ...]] = {
    "answer_correctness": (
        "answer_correctness",
        "answer_correctness_human",
        "model_judged_correctness",
    ),
    "retrieval_recall_at_k": ("retrieval_recall_at_k", "recall_at_k", "recall@k"),
    "evidence_completeness": ("evidence_set_completeness", "evidence_completeness"),
    "unsupported_claim_rate": ("unsupported_claim_rate",),
    "citation_support_rate": (
        "claim_support_rate",
        "citation_support_rate",
        "citation_precision",
    ),
    "abstention_performance": (
        "appropriate_abstention",
        "correct_abstention_rate",
        "partial_answer_accuracy",
    ),
}


def _metric_for_aliases(
    run: AnalysisRun, aliases: tuple[str, ...]
) -> MetricObservation | None:
    """Select one stored metric deterministically, preferring human judgments."""

    matching = [
        metric
        for metric in run.metrics
        if any(metric.name == alias or metric.name.startswith(alias) for alias in aliases)
        and metric.value is not None
    ]
    if not matching:
        return None
    return min(
        matching,
        key=lambda metric: (
            0 if metric.method.casefold().startswith("human") else 1,
            aliases.index(
                next(
                    alias
                    for alias in aliases
                    if metric.name == alias or metric.name.startswith(alias)
                )
            ),
            metric.version,
            metric.method,
        ),
    )


def _headline_metrics(runs: tuple[AnalysisRun, ...]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    quality_eligible = tuple(run for run in runs if not run.has_infrastructure_failure)
    for name, aliases in _HEADLINE_ALIASES.items():
        values = [
            metric.value
            for run in quality_eligible
            if (metric := _metric_for_aliases(run, aliases)) is not None
            and metric.value is not None
        ]
        output[name] = {
            "value": (sum(values) / len(values)) if values else None,
            "numerator": sum(values) if values else None,
            "denominator": len(values),
            "missing": len(quality_eligible) - len(values),
            "excluded_infrastructure": len(runs) - len(quality_eligible),
        }

    operational_dimensions = {
        "median_latency_ms": "total_latency_ms",
        "median_input_tokens": "input_tokens",
        "median_output_tokens": "output_tokens",
        "median_cost": "estimated_cost",
    }
    for name, dimension in operational_dimensions.items():
        values = [
            float(value)
            for run in runs
            if isinstance((value := run.dimension(dimension)), int | float)
        ]
        output[name] = {
            "value": _quantile(values, 0.5),
            "numerator": None,
            "denominator": len(values),
            "missing": len(runs) - len(values),
            "excluded_infrastructure": 0,
        }
    total_token_values = [
        float(input_tokens + output_tokens)
        for run in runs
        if isinstance((input_tokens := run.dimension("input_tokens")), int)
        and isinstance((output_tokens := run.dimension("output_tokens")), int)
    ]
    output["median_total_tokens"] = {
        "value": _quantile(total_token_values, 0.5),
        "numerator": None,
        "denominator": len(total_token_values),
        "missing": len(runs) - len(total_token_values),
        "excluded_infrastructure": 0,
    }
    infrastructure_count = sum(run.has_infrastructure_failure for run in runs)
    output["infrastructure_failure_rate"] = {
        "value": infrastructure_count / len(runs) if runs else None,
        "numerator": infrastructure_count,
        "denominator": len(runs),
        "missing": 0,
        "excluded_infrastructure": 0,
    }
    return output


def _visualization_data(runs: tuple[AnalysisRun, ...]) -> dict[str, Any]:
    names_by_id: dict[str, str] = {}
    name_counts: dict[str, int] = defaultdict(int)
    for run in runs:
        label = str(run.dimension("pipeline_name") or run.pipeline_id)
        if run.pipeline_id not in names_by_id:
            names_by_id[run.pipeline_id] = label
            name_counts[label] += 1

    def pipeline_label(pipeline_id: str) -> str:
        label = names_by_id.get(pipeline_id, pipeline_id)
        return label if name_counts[label] == 1 else f"{label} [{pipeline_id[:8]}]"

    grouped: dict[str, list[AnalysisRun]] = defaultdict(list)
    for run in runs:
        grouped[run.pipeline_id].append(run)

    latency: list[dict[str, Any]] = []
    for pipeline_id, group in sorted(grouped.items()):
        values = [
            float(value)
            for run in group
            if isinstance((value := run.dimension("total_latency_ms")), int | float)
        ]
        latency.append(
            {
                "pipeline": pipeline_label(pipeline_id),
                "n": len(values),
                "missing": len(group) - len(values),
                "infrastructure_failures": sum(
                    run.has_infrastructure_failure for run in group
                ),
                "minimum": _quantile(values, 0),
                "q1": _quantile(values, 0.25),
                "median": _quantile(values, 0.5),
                "q3": _quantile(values, 0.75),
                "maximum": _quantile(values, 1),
            }
        )

    failure_counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for run in runs:
        stage = str(run.dimension("failure_stage") or "")
        category = str(run.dimension("failure_category") or "")
        code = run.infrastructure_failure_code or ""
        if stage or category or code:
            failure_counts[(stage or "unattributed", category or "unlabelled", code)] += 1

    performance: list[dict[str, Any]] = []
    by_type_pipeline: dict[tuple[str, str], list[AnalysisRun]] = defaultdict(list)
    for run in runs:
        by_type_pipeline[
            (
                str(run.dimension("question_type") or "unclassified"),
                pipeline_label(run.pipeline_id),
            )
        ].append(run)
    for (question_type, pipeline), group in sorted(by_type_pipeline.items()):
        eligible = [run for run in group if not run.has_infrastructure_failure]
        values = [
            metric.value
            for run in eligible
            if (
                metric := _metric_for_aliases(
                    run, _HEADLINE_ALIASES["answer_correctness"]
                )
            )
            is not None
            and metric.value is not None
        ]
        performance.append(
            {
                "question_type": question_type,
                "pipeline": pipeline,
                "value": (sum(values) / len(values)) if values else None,
                "n": len(values),
                "denominator": len(eligible),
                "missing": len(eligible) - len(values),
                "excluded_infrastructure": len(group) - len(eligible),
            }
        )

    cost_correctness: list[dict[str, Any]] = []
    for run in runs:
        metric = _metric_for_aliases(run, _HEADLINE_ALIASES["answer_correctness"])
        cost = run.dimension("estimated_cost")
        if (
            not run.has_infrastructure_failure
            and metric is not None
            and metric.value is not None
            and isinstance(cost, int | float)
        ):
            cost_correctness.append(
                {
                    "run_id": run.run_id,
                    "pipeline": pipeline_label(run.pipeline_id),
                    "correctness": metric.value,
                    "cost": float(cost),
                    "currency": run.dimension("cost_currency"),
                }
            )

    return {
        "cost_correctness": cost_correctness,
        "latency_by_pipeline": latency,
        "failure_distribution": [
            {
                "stage": stage,
                "category": category,
                "code": code or None,
                "count": count,
            }
            for (stage, category, code), count in sorted(failure_counts.items())
        ],
        "performance_by_question_type": performance,
    }


def _available_dimensions(runs: tuple[AnalysisRun, ...]) -> dict[str, tuple[str, ...]]:
    dimensions = {
        "question_types": "question_type",
        "difficulties": "difficulty",
        "answerabilities": "answerability",
        "pipeline_modes": "pipeline_mode",
        "failure_stages": "failure_stage",
        "failure_categories": "failure_category",
    }
    output: dict[str, tuple[str, ...]] = {}
    for output_name, dimension in dimensions.items():
        output[output_name] = tuple(
            sorted(
                {
                    str(value)
                    for run in runs
                    if (value := run.dimension(dimension)) is not None
                }
            )
        )
    output["failure_codes"] = tuple(
        sorted(
            {
                run.infrastructure_failure_code
                for run in runs
                if run.infrastructure_failure_code is not None
            }
        )
    )
    return output


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    remainder = position - lower
    if lower + 1 >= len(ordered):
        return ordered[lower]
    return ordered[lower] + remainder * (ordered[lower + 1] - ordered[lower])


def _selectors(runs: tuple[AnalysisRun, ...]) -> tuple[MetricSelector, ...]:
    identities = {
        (metric.name, metric.version, metric.scope, metric.method)
        for run in runs
        for metric in run.metrics
    }
    if not identities:
        identities.add(("failure_rate", "operational-metrics.v1", "overall", "operational"))
    return tuple(
        MetricSelector(
            name=name,
            version=version,
            scope=scope,
            method=method,
            denominator_policy=(
                _operational_policy()
                if method == "operational" or name == "failure_rate"
                else _quality_policy()
            ),
        )
        for name, version, scope, method in sorted(identities)
    )


def _quality_policy() -> DenominatorPolicy:
    return DenominatorPolicy(
        exclude_infrastructure_failures=True,
        description=(
            "Quality denominator excludes documented infrastructure failures; "
            "missing metric values are excluded and counted separately."
        ),
    )


def _operational_policy() -> DenominatorPolicy:
    return DenominatorPolicy(
        exclude_infrastructure_failures=False,
        description=(
            "Operational denominator includes infrastructure failures when the "
            "measurement exists; missing values are excluded and counted separately."
        ),
    )


def _filter_conditions(filters: AnalysisFilters) -> tuple[FilterCondition, ...]:
    conditions: list[FilterCondition] = []
    if filters.pipeline_ids:
        conditions.append(FilterCondition("pipeline_id", filters.pipeline_ids))
    if filters.question_types:
        conditions.append(FilterCondition("question_type", filters.question_types))
    if filters.difficulties:
        conditions.append(FilterCondition("difficulty", filters.difficulties))
    if filters.run_statuses:
        conditions.append(FilterCondition("run_status", filters.run_statuses))
    if filters.answerabilities:
        conditions.append(FilterCondition("answerability", filters.answerabilities))
    if filters.pipeline_modes:
        conditions.append(FilterCondition("pipeline_mode", filters.pipeline_modes))
    if filters.failure_stages:
        conditions.append(FilterCondition("failure_stage", filters.failure_stages))
    if filters.failure_categories:
        conditions.append(FilterCondition("failure_category", filters.failure_categories))
    if filters.failure_codes:
        conditions.append(FilterCondition("infrastructure_failure_code", filters.failure_codes))
    if not filters.include_infrastructure_failures:
        conditions.append(FilterCondition("infrastructure_failure_code", (None,)))
    return tuple(conditions)


def _matches(run: AnalysisRun, filters: AnalysisFilters) -> bool:
    return (
        (not filters.pipeline_ids or run.pipeline_id in filters.pipeline_ids)
        and (
            not filters.question_types
            or run.dimension("question_type") in filters.question_types
        )
        and (not filters.difficulties or run.dimension("difficulty") in filters.difficulties)
        and (not filters.run_statuses or run.run_status in filters.run_statuses)
        and (
            not filters.answerabilities
            or run.dimension("answerability") in filters.answerabilities
        )
        and (
            not filters.pipeline_modes
            or run.dimension("pipeline_mode") in filters.pipeline_modes
        )
        and (
            not filters.failure_stages
            or run.dimension("failure_stage") in filters.failure_stages
        )
        and (
            not filters.failure_categories
            or run.dimension("failure_category") in filters.failure_categories
        )
        and (
            not filters.failure_codes
            or run.infrastructure_failure_code in filters.failure_codes
        )
        and (filters.include_infrastructure_failures or not run.has_infrastructure_failure)
    )


def _route_identifier(run: QueryRun) -> str:
    route = run.route_decision or {}
    retrieval_mode = str(route.get("retrieval_mode") or "none")
    reranking = bool(route.get("reranking_enabled", False))
    rewriting = bool(route.get("rewriting_enabled", False))
    return f"{retrieval_mode}|rerank={int(reranking)}|rewrite={int(rewriting)}"


def _adaptive_analysis(
    experiment: Experiment,
    runs: tuple[AnalysisRun, ...],
) -> dict[str, Any] | None:
    configuration = ExperimentAnalysisConfiguration.model_validate(
        experiment.analysis_configuration or {}
    )
    if (
        configuration.adaptive_pipeline_id is None
        or configuration.fixed_reference_pipeline_id is None
        or not configuration.best_observed_fixed_pipeline_ids
    ):
        return None
    adaptive_id = str(configuration.adaptive_pipeline_id)
    reference_id = str(configuration.fixed_reference_pipeline_id)
    candidate_ids = {str(value) for value in configuration.best_observed_fixed_pipeline_ids}
    grouped: dict[tuple[str, object], list[AnalysisRun]] = defaultdict(list)
    for run in runs:
        grouped[(run.question_id, run.dimension("repetition"))].append(run)
    criteria = BestObservedCriteria(
        quality_metric_name=configuration.quality_metric.name,
        higher_quality_is_better=configuration.quality_metric.higher_is_better,
        tie_breakers=configuration.tie_breakers,
    )
    comparisons: list[dict[str, Any]] = []
    route_distribution: dict[str, int] = defaultdict(int)
    for (question_id, repetition), group in sorted(grouped.items()):
        adaptive = next((value for value in group if value.pipeline_id == adaptive_id), None)
        reference = next((value for value in group if value.pipeline_id == reference_id), None)
        candidates = [value for value in group if value.pipeline_id in candidate_ids]
        if adaptive is None or reference is None or not candidates:
            continue
        adaptive_observation = _run_observation(adaptive, configuration)
        comparison = compare_adaptive_to_fixed(
            adaptive=adaptive_observation,
            fixed=_run_observation(reference, configuration),
            observed_candidates=[_run_observation(value, configuration) for value in candidates],
            criteria=criteria,
        )
        route_distribution[adaptive_observation.route_identifier] += 1
        comparisons.append(
            {
                "question_id": question_id,
                "repetition": repetition,
                "adaptive_run_id": adaptive.run_id,
                "fixed_reference_run_id": reference.run_id,
                **comparison.model_dump(mode="json"),
            }
        )
    numeric_fields = (
        "route_accuracy_against_best_observed",
        "answer_quality_delta",
        "quality_loss",
        "retrieval_calls_avoided",
        "reranking_calls_avoided",
        "token_reduction",
        "estimated_cost_reduction",
        "latency_reduction_ms",
    )
    aggregates: dict[str, dict[str, float | int | None]] = {}
    for field in numeric_fields:
        values = [float(row[field]) for row in comparisons if row.get(field) is not None]
        aggregates[field] = {
            "mean": (sum(values) / len(values)) if values else None,
            "denominator": len(values),
            "missing": len(comparisons) - len(values),
        }
    return {
        "configuration": configuration.model_dump(mode="json"),
        "matched_comparison_count": len(comparisons),
        "route_distribution": dict(sorted(route_distribution.items())),
        "aggregates": aggregates,
        "comparisons": comparisons,
    }


def _run_observation(
    run: AnalysisRun,
    configuration: ExperimentAnalysisConfiguration,
) -> RunObservation:
    quality = next(
        (
            metric.value
            for metric in run.metrics
            if metric.name == configuration.quality_metric.name
            and metric.version == configuration.quality_metric.version
            and metric.scope == configuration.quality_metric.scope
            and metric.method == configuration.quality_metric.method
        ),
        None,
    )
    return RunObservation(
        route_identifier=str(run.dimension("route_identifier") or run.pipeline_id),
        quality_value=quality,
        retrieval_calls=_metric_integer(run, "retrieval_calls"),
        reranking_calls=_metric_integer(run, "reranking_calls"),
        total_tokens=_optional_integer(
            run.dimension("input_tokens"), run.dimension("output_tokens")
        ),
        estimated_cost=_optional_float(run.dimension("estimated_cost")),
        latency_ms=_single_optional_integer(run.dimension("total_latency_ms")),
        infrastructure_failure=run.has_infrastructure_failure,
    )


def _metric_integer(run: AnalysisRun, name: str) -> int | None:
    value = next((metric.value for metric in run.metrics if metric.name == name), None)
    return int(value) if value is not None else None


def _optional_integer(
    left: str | int | float | bool | None,
    right: str | int | float | bool | None,
) -> int | None:
    if left is None or right is None:
        return None
    return int(left) + int(right)


def _single_optional_integer(value: str | int | float | bool | None) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: str | int | float | bool | None) -> float | None:
    return float(value) if value is not None else None


def _element_chunks(session: Session, corpus_version_id: UUID) -> dict[UUID, set[UUID]]:
    result: dict[UUID, set[UUID]] = defaultdict(set)
    for chunk in session.scalars(select(Chunk).where(Chunk.corpus_version_id == corpus_version_id)):
        for raw_id in chunk.source_element_ids:
            result[UUID(str(raw_id))].add(chunk.id)
    return result


def _survival(
    question: BenchmarkQuestion | None,
    retrieval_rows: list[RetrievalResultRecord],
    context_rows: list[ContextSource],
    element_chunks: dict[UUID, set[UUID]],
) -> EvidenceSurvivalObservation | None:
    if question is None:
        return None
    alternatives: list[frozenset[UUID]] = []
    for evidence_set in question.evidence_sets:
        required: set[UUID] = set()
        for reference in evidence_set.references:
            if reference.evidence_role not in {"required", "alternative"}:
                continue
            if reference.chunk_id is not None:
                required.add(reference.chunk_id)
            elif reference.element_id is not None:
                required.update(element_chunks.get(reference.element_id, set()))
        if required:
            alternatives.append(frozenset(required))
    if not alternatives and question.required_chunk_ids:
        alternatives.append(frozenset(UUID(value) for value in question.required_chunk_ids))
    if not alternatives:
        return None
    retrieved = {row.chunk_id for row in retrieval_rows}
    has_reranking = any(row.reranked_rank is not None for row in retrieval_rows)
    reranked = (
        {row.chunk_id for row in retrieval_rows if row.reranked_rank is not None}
        if has_reranking
        else retrieved
    )
    selected = {row.chunk_id for row in context_rows if row.selected}
    return EvidenceSurvivalObservation(
        retrieval=_best_coverage(retrieved, alternatives),
        reranking=_best_coverage(reranked, alternatives),
        context=_best_coverage(selected, alternatives),
    )


def _best_coverage(found: set[UUID], alternatives: list[frozenset[UUID]]) -> float:
    return max(len(found & required) / len(required) for required in alternatives)


def _redacted_dict(value: dict[str, Any]) -> dict[str, Any]:
    safe = redact(value, sensitive_values=configured_sensitive_values())
    return safe if isinstance(safe, dict) else {"value": safe}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
