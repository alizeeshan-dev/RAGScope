"""Pure grouped aggregation with auditable denominator accounting."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence

from backend.app.analysis.contracts import (
    AggregateMetric,
    AggregationPlan,
    AggregationResult,
    AnalysisRun,
    DenominatorPolicy,
    DimensionValue,
    FilterCondition,
    MetricObservation,
    MetricSelector,
)


def aggregate_metrics(
    runs: Sequence[AnalysisRun], plan: AggregationPlan
) -> AggregationResult:
    """Aggregate exact metric identities without silently changing denominators.

    Filters are applied first. Within every resulting group, each run is exactly
    one of: contributing, missing a usable metric value, or excluded for a
    documented infrastructure failure under the selector's explicit policy.
    Mean and median remain ``None`` when the denominator is empty.
    """

    _validate_unique_runs(runs)
    filtered = tuple(run for run in runs if _matches_filters(run, plan.filters))
    grouped: dict[tuple[tuple[str, DimensionValue], ...], list[AnalysisRun]] = defaultdict(list)
    for run in filtered:
        group = tuple((dimension, run.dimension(dimension)) for dimension in plan.group_by)
        grouped[group].append(run)
    # A global aggregation has a meaningful empty population. Preserve a row
    # with denominator zero so an empty filter result is visible, not absent.
    if not plan.group_by and not grouped:
        grouped[()] = []

    aggregates: list[AggregateMetric] = []
    for group in sorted(grouped, key=_canonical_group):
        group_runs = sorted(grouped[group], key=lambda run: run.run_id)
        for selector in sorted(plan.selectors, key=lambda item: item.identity):
            aggregates.append(_aggregate_selector(group, group_runs, selector))

    return AggregationResult(
        plan=plan,
        source_run_count=len(runs),
        filtered_run_count=len(filtered),
        filtered_run_ids=tuple(sorted(run.run_id for run in filtered)),
        aggregates=tuple(aggregates),
    )


def aggregate_evidence_survival(
    runs: Sequence[AnalysisRun],
    *,
    metric_version: str,
    denominator_policy: DenominatorPolicy,
    group_by: tuple[str, ...] = (),
    filters: tuple[FilterCondition, ...] = (),
) -> AggregationResult:
    """Aggregate retrieval→reranking→context survival as three explicit metrics.

    A run with no survival annotation, a version mismatch, or a missing stage
    value contributes to that stage's missing count rather than an invented zero.
    """

    projected: list[AnalysisRun] = []
    for run in runs:
        survival = run.evidence_survival
        metrics: tuple[MetricObservation, ...] = ()
        if survival is not None and survival.metric_version == metric_version:
            metrics = tuple(
                MetricObservation(
                    name=f"evidence_survival.{stage}",
                    version=metric_version,
                    scope="context",
                    method="deterministic_human_benchmark_evidence",
                    value=getattr(survival, stage),
                    details={"stage": stage},
                )
                for stage in ("retrieval", "reranking", "context")
            )
        projected.append(
            AnalysisRun(
                run_id=run.run_id,
                pipeline_id=run.pipeline_id,
                question_id=run.question_id,
                run_status=run.run_status,
                infrastructure_failure_code=run.infrastructure_failure_code,
                dimensions=dict(run.dimensions),
                metrics=metrics,
                evidence_survival=survival,
            )
        )
    selectors = tuple(
        MetricSelector(
            name=f"evidence_survival.{stage}",
            version=metric_version,
            scope="context",
            method="deterministic_human_benchmark_evidence",
            denominator_policy=denominator_policy,
        )
        for stage in ("retrieval", "reranking", "context")
    )
    return aggregate_metrics(
        projected,
        AggregationPlan(selectors=selectors, group_by=group_by, filters=filters),
    )


def _aggregate_selector(
    group: tuple[tuple[str, DimensionValue], ...],
    runs: Sequence[AnalysisRun],
    selector: MetricSelector,
) -> AggregateMetric:
    values: list[float] = []
    contributing: list[str] = []
    missing: list[str] = []
    excluded: list[str] = []
    for run in runs:
        if (
            selector.denominator_policy.exclude_infrastructure_failures
            and run.has_infrastructure_failure
        ):
            excluded.append(run.run_id)
            continue
        matches = [metric for metric in run.metrics if _matches_selector(metric, selector)]
        if len(matches) > 1:
            raise ValueError(
                f"run {run.run_id!r} has duplicate metric identity {selector.identity!r}"
            )
        if not matches or matches[0].value is None:
            missing.append(run.run_id)
            continue
        value = matches[0].value
        assert value is not None
        values.append(value)
        contributing.append(run.run_id)

    value_sum = float(sum(values)) if values else None
    mean = value_sum / len(values) if value_sum is not None else None
    median = float(statistics.median(values)) if values else None
    return AggregateMetric(
        group=tuple((key, value) for key, value in group),
        metric_name=selector.name,
        metric_version=selector.version,
        metric_scope=selector.scope,
        evaluation_method=selector.method,
        denominator_policy=selector.denominator_policy,
        total_run_count=len(runs),
        denominator_count=len(values),
        missing_metric_count=len(missing),
        excluded_infrastructure_count=len(excluded),
        value_sum=value_sum,
        mean=mean,
        median=median,
        contributing_run_ids=tuple(contributing),
        missing_run_ids=tuple(missing),
        excluded_run_ids=tuple(excluded),
    )


def _matches_selector(metric: MetricObservation, selector: MetricSelector) -> bool:
    return (metric.name, metric.version, metric.scope, metric.method) == selector.identity


def _matches_filters(run: AnalysisRun, filters: Iterable[FilterCondition]) -> bool:
    return all(run.dimension(item.dimension) in item.allowed_values for item in filters)


def _validate_unique_runs(runs: Sequence[AnalysisRun]) -> None:
    ids = [run.run_id for run in runs]
    if len(set(ids)) != len(ids):
        raise ValueError("analysis input contains duplicate run IDs")


def _canonical_group(group: tuple[tuple[str, DimensionValue], ...]) -> str:
    return json.dumps(group, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
