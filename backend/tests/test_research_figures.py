from __future__ import annotations

import json
from pathlib import Path

import pytest
from backend.app.analysis.aggregation import aggregate_metrics
from backend.app.analysis.contracts import (
    AggregationPlan,
    AnalysisRun,
    DenominatorPolicy,
    EvidenceSurvivalObservation,
    MetricObservation,
    MetricSelector,
)
from backend.app.analysis.exports import build_export_bundle
from backend.app.research.contracts import (
    ExactMetric,
    ResearchFigureConfiguration,
)
from backend.app.research.figures import generate_required_figures
from backend.app.research.io import load_analysis_export, write_figure_bundle

QUALITY = DenominatorPolicy(True, "non-infrastructure runs with a present quality value")
OPERATIONAL = DenominatorPolicy(False, "all runs with a present operational value")
RECALL = ExactMetric(
    "recall_at_k",
    "retrieval-evidence-v1",
    "retrieval",
    "deterministic_human_benchmark_evidence",
    QUALITY,
)
CORRECTNESS = ExactMetric(
    "answer_correctness", "generation-quality-v1", "generation", "human", QUALITY
)
SUPPORT = ExactMetric(
    "claim_support_rate", "citation-metrics-v1", "citation", "automated", QUALITY
)
COST = ExactMetric(
    "estimated_cost", "operational-metrics.v1", "cost", "operational", OPERATIONAL
)
LATENCY = ExactMetric(
    "total_latency_ms", "operational-metrics.v1", "overall", "operational", OPERATIONAL
)
CONFIGURATION = ResearchFigureConfiguration(
    retrieval_recall=RECALL,
    answer_correctness=CORRECTNESS,
    citation_support_rate=SUPPORT,
    estimated_cost=COST,
    total_latency=LATENCY,
    evidence_survival_version="evidence-survival.v1",
    evidence_survival_policy=QUALITY,
)


def _observation(metric: ExactMetric, value: float | None) -> MetricObservation:
    return MetricObservation(
        name=metric.name,
        version=metric.version,
        scope=metric.scope,
        method=metric.method,
        value=value,
    )


def _run(
    run_id: str,
    pipeline: str,
    *,
    recall: float | None,
    correctness: float | None,
    support: float | None,
    cost: float | None,
    latency: float | None,
    question_type: str | None,
    failure_stage: str | None,
    infrastructure_failure: str | None = None,
    survival: tuple[float | None, float | None, float | None] | None = None,
) -> AnalysisRun:
    dimensions: dict[str, str | int | float | bool | None] = {
        "pipeline_name": pipeline.title(),
        "question_type": question_type,
        "primary_failure_stage": failure_stage,
    }
    return AnalysisRun(
        run_id=run_id,
        pipeline_id=pipeline,
        question_id=f"q-{run_id}",
        run_status="failed" if infrastructure_failure else "succeeded",
        infrastructure_failure_code=infrastructure_failure,
        dimensions=dimensions,
        metrics=(
            _observation(RECALL, recall),
            _observation(CORRECTNESS, correctness),
            _observation(SUPPORT, support),
            _observation(COST, cost),
            _observation(LATENCY, latency),
        ),
        evidence_survival=(
            EvidenceSurvivalObservation(*survival) if survival is not None else None
        ),
    )


@pytest.fixture
def runs() -> tuple[AnalysisRun, ...]:
    return (
        _run(
            "h1",
            "hybrid",
            recall=1.0,
            correctness=1.0,
            support=1.0,
            cost=0.10,
            latency=100,
            question_type="direct_fact",
            failure_stage="generation",
            survival=(1.0, 0.8, 0.7),
        ),
        _run(
            "h2",
            "hybrid",
            recall=0.5,
            correctness=None,
            support=0.5,
            cost=0.05,
            latency=80,
            question_type="comparison",
            failure_stage=None,
            survival=(0.5, 0.5, None),
        ),
        _run(
            "l1",
            "lexical",
            recall=0.9,
            correctness=0.9,
            support=0.9,
            cost=0.04,
            latency=50,
            question_type="direct_fact",
            failure_stage=None,
            infrastructure_failure="TIMEOUT",
            survival=(0.9, 0.9, 0.9),
        ),
        _run(
            "l2",
            "lexical",
            recall=0.0,
            correctness=0.0,
            support=0.0,
            cost=0.02,
            latency=200,
            question_type="direct_fact",
            failure_stage="retrieval",
            survival=(0.0, 0.0, 0.0),
        ),
    )


def test_generates_all_eight_required_figures_with_sample_audits(
    runs: tuple[AnalysisRun, ...],
) -> None:
    artifacts = generate_required_figures(runs, CONFIGURATION)

    assert [artifact.spec.figure_id for artifact in artifacts] == [
        "01_retrieval_recall_by_pipeline",
        "02_answer_correctness_by_pipeline",
        "03_citation_support_by_pipeline",
        "04_cost_vs_answer_correctness",
        "05_latency_distribution",
        "06_failure_stage_distribution",
        "07_performance_by_query_type",
        "08_required_evidence_survival",
    ]
    assert all(artifact.spec.status == "ready" for artifact in artifacts)
    assert all(artifact.spec.samples for artifact in artifacts)
    assert all("denominator total n=" in artifact.svg for artifact in artifacts)


def test_missing_quality_is_not_plotted_as_zero(runs: tuple[AnalysisRun, ...]) -> None:
    correctness = generate_required_figures(runs, CONFIGURATION)[1].spec
    hybrid = next(item for item in correctness.data if item.series == "Hybrid")
    hybrid_sample = next(item for item in correctness.samples if item.group == "Hybrid")

    # h2 is missing, so the mean is h1's 1.0 rather than an invented (1 + 0) / 2.
    assert hybrid.value == 1.0
    assert hybrid_sample.total_run_count == 2
    assert hybrid_sample.denominator_count == 1
    assert hybrid_sample.missing_count == 1
    assert hybrid_sample.missing_run_ids == ("h2",)


def test_infrastructure_is_separate_and_operational_latency_can_include_it(
    runs: tuple[AnalysisRun, ...],
) -> None:
    artifacts = generate_required_figures(runs, CONFIGURATION)
    recall = artifacts[0].spec
    latency = artifacts[4].spec
    failures = artifacts[5].spec

    lexical_recall = next(item for item in recall.samples if item.group == "Lexical")
    lexical_latency = next(item for item in latency.samples if item.group == "Lexical")
    assert lexical_recall.excluded_infrastructure_count == 1
    assert lexical_recall.denominator_count == 1
    assert lexical_latency.excluded_infrastructure_count == 0
    assert lexical_latency.denominator_count == 2
    infrastructure = next(
        item
        for item in failures.data
        if item.series == "Lexical" and item.category == "infrastructure"
    )
    assert infrastructure.count == 1
    assert infrastructure.value == 0.5


def test_evidence_survival_has_stage_specific_denominators(
    runs: tuple[AnalysisRun, ...],
) -> None:
    survival = generate_required_figures(runs, CONFIGURATION)[7].spec
    hybrid_context = next(
        item
        for item in survival.data
        if item.series == "Hybrid" and item.category == "context"
    )
    context_sample = next(item for item in survival.samples if item.group == "Hybrid / context")

    assert hybrid_context.value == 0.7
    assert context_sample.denominator_count == 1
    assert context_sample.missing_count == 1
    assert context_sample.missing_run_ids == ("h2",)


def test_empty_or_incomplete_data_produces_placeholders_not_zero_marks() -> None:
    artifacts = generate_required_figures((), CONFIGURATION)
    assert len(artifacts) == 8
    assert all(artifact.spec.status == "incomplete" for artifact in artifacts)
    assert all(not artifact.spec.data for artifact in artifacts)
    assert all("INCOMPLETE DATA" in artifact.svg for artifact in artifacts)
    assert all("no zero values were invented" in artifact.svg for artifact in artifacts)


def test_exact_metric_identity_does_not_substitute_model_judge_for_human() -> None:
    run = AnalysisRun(
        run_id="run",
        pipeline_id="pipeline",
        question_id="question",
        run_status="succeeded",
        metrics=(
            MetricObservation(
                "answer_correctness",
                "judge-v1",
                "generation",
                "model_judge",
                1.0,
            ),
        ),
    )
    correctness = generate_required_figures((run,), CONFIGURATION)[1].spec
    assert correctness.status == "incomplete"
    assert correctness.data == ()
    assert correctness.samples[0].missing_count == 1


def test_loads_real_analysis_export_shape_without_database_access(
    runs: tuple[AnalysisRun, ...], tmp_path: Path
) -> None:
    selector = MetricSelector(
        RECALL.name,
        RECALL.version,
        RECALL.scope,
        RECALL.method,
        RECALL.denominator_policy,
    )
    result = aggregate_metrics(runs, AggregationPlan(selectors=(selector,)))
    bundle = build_export_bundle(runs, result)
    source = tmp_path / "analysis.json"
    source.write_text(bundle.json_document, encoding="utf-8")

    dataset = load_analysis_export(source)

    assert [run.run_id for run in dataset.runs] == [run.run_id for run in runs]
    assert all(len(run.metrics) == 5 for run in dataset.runs)
    assert len(generate_required_figures(dataset.runs, CONFIGURATION)) == 8


def test_generated_writer_is_deterministic_and_restricted_to_generated_root(
    runs: tuple[AnalysisRun, ...], tmp_path: Path
) -> None:
    artifacts = generate_required_figures(runs, CONFIGURATION)
    generated_root = tmp_path / "artifacts" / "research"
    output = generated_root / "pilot"

    first = write_figure_bundle(
        artifacts,
        output,
        generated_root=generated_root,
        source_export_sha256="a" * 64,
        configuration_sha256="b" * 64,
    )
    contents = {path.name: path.read_bytes() for path in first}
    second = write_figure_bundle(
        artifacts,
        output,
        generated_root=generated_root,
        source_export_sha256="a" * 64,
        configuration_sha256="b" * 64,
    )

    assert len(first) == 17
    assert first == second
    assert contents == {path.name: path.read_bytes() for path in second}
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["figures"]) == 8
    assert manifest["source_export_sha256"] == "a" * 64
    assert manifest["configuration_sha256"] == "b" * 64
    with pytest.raises(ValueError, match="generated root"):
        write_figure_bundle(artifacts, tmp_path / "outside", generated_root=generated_root)


def test_duplicate_exact_metric_per_run_is_rejected() -> None:
    metric = _observation(RECALL, 1.0)
    run = AnalysisRun(
        run_id="duplicate",
        pipeline_id="pipeline",
        question_id="question",
        run_status="succeeded",
        metrics=(metric, metric),
    )
    with pytest.raises(ValueError, match="duplicate metric"):
        generate_required_figures((run,), CONFIGURATION)
