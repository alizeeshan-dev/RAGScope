from __future__ import annotations

import csv
import json
from io import StringIO

import pytest
from backend.app.analysis.aggregation import (
    aggregate_evidence_survival,
    aggregate_metrics,
)
from backend.app.analysis.contracts import (
    ANALYSIS_EXPORT_SCHEMA_VERSION,
    EVIDENCE_SURVIVAL_METRIC_VERSION,
    AggregationPlan,
    AnalysisRun,
    DenominatorPolicy,
    EvidenceSurvivalObservation,
    FilterCondition,
    MetricObservation,
    MetricSelector,
)
from backend.app.analysis.exports import build_export_bundle

QUALITY_POLICY = DenominatorPolicy(
    exclude_infrastructure_failures=True,
    description="successful runs with a present human correctness label",
)
ALL_RUNS_POLICY = DenominatorPolicy(
    exclude_infrastructure_failures=False,
    description="all runs with a present operational value",
)
QUALITY_SELECTOR = MetricSelector(
    name="answer_correctness",
    version="human-review.v1",
    scope="generation",
    method="human",
    denominator_policy=QUALITY_POLICY,
)


def _metric(value: float | None) -> MetricObservation:
    return MetricObservation(
        name="answer_correctness",
        version="human-review.v1",
        scope="generation",
        method="human",
        value=value,
        details={"reviewed": value is not None},
    )


def _run(
    identifier: str,
    *,
    pipeline: str = "hybrid",
    value: float | None = None,
    include_metric: bool = True,
    infrastructure_failure: str | None = None,
    question_type: str = "direct_fact",
    difficulty: str = "medium",
    survival: EvidenceSurvivalObservation | None = None,
) -> AnalysisRun:
    return AnalysisRun(
        run_id=identifier,
        pipeline_id=pipeline,
        question_id=f"question-{identifier}",
        run_status="failed" if infrastructure_failure else "succeeded",
        infrastructure_failure_code=infrastructure_failure,
        dimensions={"question_type": question_type, "difficulty": difficulty},
        metrics=(_metric(value),) if include_metric else (),
        evidence_survival=survival,
    )


def test_explicit_denominator_missing_and_infrastructure_accounting() -> None:
    runs = (
        _run("r1", value=1.0),
        _run("r2", value=0.5),
        _run("r3", value=None),
        _run("r4", value=0.9, infrastructure_failure="MODEL_PROVIDER_FAILURE"),
        _run("r5", include_metric=False),
    )

    result = aggregate_metrics(runs, AggregationPlan(selectors=(QUALITY_SELECTOR,)))
    aggregate = result.aggregates[0]

    assert aggregate.total_run_count == 5
    assert aggregate.denominator_count == 2
    assert aggregate.missing_metric_count == 2
    assert aggregate.excluded_infrastructure_count == 1
    assert aggregate.value_sum == 1.5
    assert aggregate.mean == 0.75
    assert aggregate.median == 0.75
    assert aggregate.contributing_run_ids == ("r1", "r2")
    assert aggregate.missing_run_ids == ("r3", "r5")
    assert aggregate.excluded_run_ids == ("r4",)


def test_empty_denominator_is_missing_not_zero() -> None:
    aggregate = aggregate_metrics(
        (_run("r1", value=None), _run("r2", include_metric=False)),
        AggregationPlan(selectors=(QUALITY_SELECTOR,)),
    ).aggregates[0]

    assert aggregate.denominator_count == 0
    assert aggregate.value_sum is None
    assert aggregate.mean is None
    assert aggregate.median is None
    assert aggregate.missing_metric_count == 2


def test_empty_filtered_population_still_has_an_explicit_global_denominator() -> None:
    result = aggregate_metrics(
        (_run("r1", value=1.0),),
        AggregationPlan(
            selectors=(QUALITY_SELECTOR,),
            filters=(FilterCondition("difficulty", ("not-present",)),),
        ),
    )

    assert result.filtered_run_count == 0
    assert len(result.aggregates) == 1
    assert result.aggregates[0].total_run_count == 0
    assert result.aggregates[0].denominator_count == 0
    assert result.aggregates[0].mean is None


def test_median_is_hand_calculated_for_even_sample() -> None:
    runs = tuple(
        _run(f"r{index}", value=value) for index, value in enumerate((1, 9, 3, 7))
    )
    result = aggregate_metrics(runs, AggregationPlan(selectors=(QUALITY_SELECTOR,)))
    aggregate = result.aggregates[0]
    # Sorted values are 1, 3, 7, 9: both mean and median are 5.
    assert aggregate.value_sum == 20.0
    assert aggregate.mean == 5.0
    assert aggregate.median == 5.0


def test_grouping_and_filters_have_visible_per_group_denominators() -> None:
    runs = (
        _run("h1", pipeline="hybrid", value=1.0),
        _run("h2", pipeline="hybrid", value=None),
        _run("l1", pipeline="lexical", value=0.5),
        _run("l2", pipeline="lexical", value=0.0, difficulty="hard"),
    )
    result = aggregate_metrics(
        runs,
        AggregationPlan(
            selectors=(QUALITY_SELECTOR,),
            group_by=("pipeline_id", "question_type"),
            filters=(FilterCondition("difficulty", ("medium",)),),
        ),
    )

    assert result.source_run_count == 4
    assert result.filtered_run_count == 3
    assert result.filtered_run_ids == ("h1", "h2", "l1")
    by_pipeline = {dict(item.group)["pipeline_id"]: item for item in result.aggregates}
    assert by_pipeline["hybrid"].total_run_count == 2
    assert by_pipeline["hybrid"].denominator_count == 1
    assert by_pipeline["hybrid"].missing_metric_count == 1
    assert by_pipeline["lexical"].total_run_count == 1
    assert by_pipeline["lexical"].mean == 0.5


def test_infrastructure_inclusion_is_an_explicit_per_metric_choice() -> None:
    include_selector = MetricSelector(
        name=QUALITY_SELECTOR.name,
        version=QUALITY_SELECTOR.version,
        scope=QUALITY_SELECTOR.scope,
        method=QUALITY_SELECTOR.method,
        denominator_policy=ALL_RUNS_POLICY,
    )
    runs = (
        _run("success", value=1.0),
        _run("failure", value=0.0, infrastructure_failure="TIMEOUT"),
    )
    included = aggregate_metrics(
        runs, AggregationPlan(selectors=(include_selector,))
    ).aggregates[0]
    excluded = aggregate_metrics(
        runs, AggregationPlan(selectors=(QUALITY_SELECTOR,))
    ).aggregates[0]

    assert (included.denominator_count, included.mean) == (2, 0.5)
    assert included.excluded_infrastructure_count == 0
    assert (excluded.denominator_count, excluded.mean) == (1, 1.0)
    assert excluded.excluded_infrastructure_count == 1


def test_duplicate_exact_metric_identity_is_rejected() -> None:
    run = _run("duplicate", value=1.0)
    duplicate = AnalysisRun(
        run_id=run.run_id,
        pipeline_id=run.pipeline_id,
        question_id=run.question_id,
        run_status=run.run_status,
        dimensions=run.dimensions,
        metrics=(run.metrics[0], run.metrics[0]),
    )
    with pytest.raises(ValueError, match="duplicate metric identity"):
        aggregate_metrics((duplicate,), AggregationPlan(selectors=(QUALITY_SELECTOR,)))


def test_evidence_survival_uses_stage_specific_missing_denominators() -> None:
    runs = (
        _run(
            "r1",
            survival=EvidenceSurvivalObservation(retrieval=1.0, reranking=0.5, context=0.5),
        ),
        _run(
            "r2",
            survival=EvidenceSurvivalObservation(retrieval=0.5, reranking=None, context=0.0),
        ),
        _run("r3", survival=None),
    )
    result = aggregate_evidence_survival(
        runs,
        metric_version=EVIDENCE_SURVIVAL_METRIC_VERSION,
        denominator_policy=QUALITY_POLICY,
    )
    values = {item.metric_name: item for item in result.aggregates}

    assert values["evidence_survival.retrieval"].mean == 0.75
    assert values["evidence_survival.retrieval"].denominator_count == 2
    assert values["evidence_survival.reranking"].mean == 0.5
    assert values["evidence_survival.reranking"].denominator_count == 1
    assert values["evidence_survival.reranking"].missing_metric_count == 2
    assert values["evidence_survival.context"].mean == 0.25


def test_exports_are_deterministic_tidy_versioned_and_reproduce_aggregate() -> None:
    runs = (_run("r2", value=None), _run("r1", value=1.0))
    result = aggregate_metrics(runs, AggregationPlan(selectors=(QUALITY_SELECTOR,)))

    first = build_export_bundle(runs, result)
    second = build_export_bundle(runs, result)

    assert first == second
    assert first.schema_version == ANALYSIS_EXPORT_SCHEMA_VERSION
    assert "\r\n" not in first.run_metrics_csv
    raw_rows = list(csv.DictReader(StringIO(first.run_metrics_csv)))
    assert [row["run_id"] for row in raw_rows] == ["r1", "r2"]
    assert raw_rows[1]["metric_value"] == ""

    aggregate_rows = list(csv.DictReader(StringIO(first.aggregate_metrics_csv)))
    assert len(aggregate_rows) == 1
    assert aggregate_rows[0]["denominator_count"] == "1"
    assert aggregate_rows[0]["missing_metric_count"] == "1"
    assert float(aggregate_rows[0]["mean"]) == 1.0

    document = json.loads(first.json_document)
    assert document["schema_version"] == ANALYSIS_EXPORT_SCHEMA_VERSION
    assert document["aggregates"][0]["mean"] == 1.0
    assert document["aggregates"][0]["denominator_count"] == 1
    assert document["aggregates"][0]["contributing_run_ids"] == ["r1"]


def test_human_and_automatic_metrics_cannot_be_silently_combined() -> None:
    automatic = MetricObservation(
        name="answer_correctness",
        version="judge.v1",
        scope="generation",
        method="model_judge",
        value=0.1,
    )
    run = AnalysisRun(
        run_id="run",
        pipeline_id="pipeline",
        question_id="question",
        run_status="succeeded",
        metrics=(_metric(1.0), automatic),
    )
    result = aggregate_metrics((run,), AggregationPlan(selectors=(QUALITY_SELECTOR,)))
    assert result.aggregates[0].mean == 1.0
    assert result.aggregates[0].denominator_count == 1
