"""Generate the eight required research figures from exported run observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import fmean

from backend.app.analysis.contracts import AnalysisRun, DimensionValue
from backend.app.research.contracts import (
    ExactMetric,
    FigureArtifact,
    FigureDatum,
    FigureKind,
    FigureSpec,
    ResearchFigureConfiguration,
    SampleAudit,
)
from backend.app.research.svg import render_svg


def generate_required_figures(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> tuple[FigureArtifact, ...]:
    """Return all required figures in specification order.

    Only exact stored metric observations and stored dimensions are used. Missing
    or excluded values are represented in sample audits. A figure without any
    usable data is emitted with ``status=incomplete`` and an explanatory SVG.
    """

    _validate_unique_runs(runs)
    specs = (
        _metric_by_pipeline(
            runs,
            configuration,
            figure_id="01_retrieval_recall_by_pipeline",
            title="Retrieval Recall@k by pipeline",
            metric=configuration.retrieval_recall,
        ),
        _metric_by_pipeline(
            runs,
            configuration,
            figure_id="02_answer_correctness_by_pipeline",
            title="Answer correctness by pipeline",
            metric=configuration.answer_correctness,
        ),
        _metric_by_pipeline(
            runs,
            configuration,
            figure_id="03_citation_support_by_pipeline",
            title="Citation support rate by pipeline",
            metric=configuration.citation_support_rate,
        ),
        _cost_vs_correctness(runs, configuration),
        _latency_distribution(runs, configuration),
        _failure_stage_distribution(runs, configuration),
        _performance_by_query_type(runs, configuration),
        _evidence_survival(runs, configuration),
    )
    return tuple(FigureArtifact(spec=spec, svg=render_svg(spec)) for spec in specs)


def _metric_by_pipeline(
    runs: Sequence[AnalysisRun],
    configuration: ResearchFigureConfiguration,
    *,
    figure_id: str,
    title: str,
    metric: ExactMetric,
) -> FigureSpec:
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    for pipeline, group_runs in _pipeline_groups(runs, configuration).items():
        values, audit = _metric_values(group_runs, metric, pipeline)
        samples.append(audit)
        if values:
            data.append(FigureDatum(series=pipeline, category=pipeline, value=fmean(values)))
    return _spec(
        figure_id=figure_id,
        title=title,
        kind="bar",
        identities=(metric.identity,),
        data=data,
        samples=samples,
    )


def _cost_vs_correctness(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> FigureSpec:
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    cost = configuration.estimated_cost
    correctness = configuration.answer_correctness
    for pipeline, group_runs in _pipeline_groups(runs, configuration).items():
        contributing: list[str] = []
        missing: list[str] = []
        excluded: list[str] = []
        for run in group_runs:
            if run.has_infrastructure_failure and (
                cost.denominator_policy.exclude_infrastructure_failures
                or correctness.denominator_policy.exclude_infrastructure_failures
            ):
                excluded.append(run.run_id)
                continue
            cost_value = _exact_metric_value(run, cost)
            correctness_value = _exact_metric_value(run, correctness)
            if cost_value is None or correctness_value is None:
                missing.append(run.run_id)
                continue
            contributing.append(run.run_id)
            data.append(
                FigureDatum(
                    series=pipeline,
                    x=cost_value,
                    y=correctness_value,
                    run_id=run.run_id,
                )
            )
        samples.append(
            _audit(
                pipeline,
                group_runs,
                contributing,
                missing,
                excluded,
                (
                    f"paired {cost.name} and {correctness.name}; "
                    "both exact metric values must be present"
                ),
            )
        )
    return _spec(
        figure_id="04_cost_vs_answer_correctness",
        title="Estimated cost versus answer correctness",
        kind="scatter",
        identities=(cost.identity, correctness.identity),
        data=data,
        samples=samples,
    )


def _latency_distribution(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> FigureSpec:
    metric = configuration.total_latency
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    for pipeline, group_runs in _pipeline_groups(runs, configuration).items():
        values, audit = _metric_values(group_runs, metric, pipeline)
        samples.append(audit)
        contributing = iter(audit.contributing_run_ids)
        data.extend(
            FigureDatum(
                series=pipeline,
                category=pipeline,
                value=value,
                run_id=next(contributing),
            )
            for value in values
        )
    return _spec(
        figure_id="05_latency_distribution",
        title="Total latency distribution by pipeline",
        kind="distribution",
        identities=(metric.identity,),
        data=data,
        samples=samples,
    )


def _failure_stage_distribution(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> FigureSpec:
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    for pipeline, group_runs in _pipeline_groups(runs, configuration).items():
        labels: list[str] = []
        contributing: list[str] = []
        missing: list[str] = []
        for run in group_runs:
            if run.has_infrastructure_failure:
                stage: DimensionValue = "infrastructure"
            else:
                stage = run.dimension(configuration.failure_stage_dimension)
            if not isinstance(stage, str) or not stage:
                missing.append(run.run_id)
                continue
            labels.append(stage)
            contributing.append(run.run_id)
        counts = Counter(labels)
        denominator = len(labels)
        for stage, count in sorted(counts.items()):
            data.append(
                FigureDatum(
                    series=pipeline,
                    category=stage,
                    value=count / denominator,
                    count=count,
                )
            )
        samples.append(
            _audit(
                pipeline,
                group_runs,
                contributing,
                missing,
                [],
                (
                    "runs with a stored primary failure stage; documented "
                    "infrastructure failures are labelled infrastructure"
                ),
            )
        )
    return _spec(
        figure_id="06_failure_stage_distribution",
        title="Primary failure-stage distribution by pipeline",
        kind="grouped_bar",
        identities=(),
        data=data,
        samples=samples,
        extra_notes=(
            f"Failure stage dimension: {configuration.failure_stage_dimension}",
        ),
    )


def _performance_by_query_type(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> FigureSpec:
    metric = configuration.answer_correctness
    grouped: dict[tuple[str, str], list[AnalysisRun]] = defaultdict(list)
    missing_type = 0
    for run in runs:
        question_type = run.dimension(configuration.question_type_dimension)
        if not isinstance(question_type, str) or not question_type:
            missing_type += 1
            continue
        grouped[(_pipeline_label(run, configuration), question_type)].append(run)
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    for (pipeline, question_type), group_runs in sorted(grouped.items()):
        label = f"{pipeline} / {question_type}"
        values, audit = _metric_values(group_runs, metric, label)
        samples.append(audit)
        if values:
            data.append(
                FigureDatum(
                    series=pipeline,
                    category=question_type,
                    value=fmean(values),
                )
            )
    notes = (
        (f"{missing_type} run(s) omitted because question type is missing.",)
        if missing_type
        else ()
    )
    return _spec(
        figure_id="07_performance_by_query_type",
        title="Answer correctness by query type and pipeline",
        kind="grouped_bar",
        identities=(metric.identity,),
        data=data,
        samples=samples,
        extra_notes=notes,
    )


def _evidence_survival(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> FigureSpec:
    data: list[FigureDatum] = []
    samples: list[SampleAudit] = []
    policy = configuration.evidence_survival_policy
    stages = ("retrieval", "reranking", "context")
    for pipeline, group_runs in _pipeline_groups(runs, configuration).items():
        for stage in stages:
            contributing: list[str] = []
            missing: list[str] = []
            excluded: list[str] = []
            values: list[float] = []
            for run in group_runs:
                if policy.exclude_infrastructure_failures and run.has_infrastructure_failure:
                    excluded.append(run.run_id)
                    continue
                survival = run.evidence_survival
                if (
                    survival is None
                    or survival.metric_version != configuration.evidence_survival_version
                ):
                    missing.append(run.run_id)
                    continue
                value = getattr(survival, stage)
                if value is None:
                    missing.append(run.run_id)
                    continue
                values.append(value)
                contributing.append(run.run_id)
            label = f"{pipeline} / {stage}"
            samples.append(
                _audit(
                    label,
                    group_runs,
                    contributing,
                    missing,
                    excluded,
                    policy.description,
                )
            )
            if values:
                data.append(
                    FigureDatum(series=pipeline, category=stage, value=fmean(values))
                )
    identity = (
        "evidence_survival",
        configuration.evidence_survival_version,
        "context",
        "deterministic_human_benchmark_evidence",
    )
    return _spec(
        figure_id="08_required_evidence_survival",
        title="Required-evidence survival through the pipeline",
        kind="line",
        identities=(identity,),
        data=data,
        samples=samples,
    )


def _metric_values(
    runs: Sequence[AnalysisRun], metric: ExactMetric, group: str
) -> tuple[list[float], SampleAudit]:
    values: list[float] = []
    contributing: list[str] = []
    missing: list[str] = []
    excluded: list[str] = []
    for run in sorted(runs, key=lambda item: item.run_id):
        if (
            metric.denominator_policy.exclude_infrastructure_failures
            and run.has_infrastructure_failure
        ):
            excluded.append(run.run_id)
            continue
        value = _exact_metric_value(run, metric)
        if value is None:
            missing.append(run.run_id)
            continue
        values.append(value)
        contributing.append(run.run_id)
    return values, _audit(
        group,
        runs,
        contributing,
        missing,
        excluded,
        metric.denominator_policy.description,
    )


def _exact_metric_value(run: AnalysisRun, metric: ExactMetric) -> float | None:
    matches = [
        item
        for item in run.metrics
        if (item.name, item.version, item.scope, item.method) == metric.identity
    ]
    if len(matches) > 1:
        raise ValueError(f"run {run.run_id!r} has duplicate metric {metric.identity!r}")
    return matches[0].value if matches else None


def _audit(
    group: str,
    runs: Sequence[AnalysisRun],
    contributing: Sequence[str],
    missing: Sequence[str],
    excluded: Sequence[str],
    description: str,
) -> SampleAudit:
    return SampleAudit(
        group=group,
        total_run_count=len(runs),
        denominator_count=len(contributing),
        missing_count=len(missing),
        excluded_infrastructure_count=len(excluded),
        contributing_run_ids=tuple(contributing),
        missing_run_ids=tuple(missing),
        excluded_run_ids=tuple(excluded),
        denominator_description=description,
    )


def _pipeline_groups(
    runs: Sequence[AnalysisRun], configuration: ResearchFigureConfiguration
) -> dict[str, list[AnalysisRun]]:
    groups: dict[str, list[AnalysisRun]] = defaultdict(list)
    for run in runs:
        groups[_pipeline_label(run, configuration)].append(run)
    return {key: sorted(groups[key], key=lambda item: item.run_id) for key in sorted(groups)}


def _pipeline_label(run: AnalysisRun, configuration: ResearchFigureConfiguration) -> str:
    label = run.dimension(configuration.pipeline_label_dimension)
    return label if isinstance(label, str) and label else run.pipeline_id


def _spec(
    *,
    figure_id: str,
    title: str,
    kind: FigureKind,
    identities: tuple[tuple[str, str, str, str], ...],
    data: Sequence[FigureDatum],
    samples: Sequence[SampleAudit],
    extra_notes: tuple[str, ...] = (),
) -> FigureSpec:
    missing = sum(item.missing_count for item in samples)
    excluded = sum(item.excluded_infrastructure_count for item in samples)
    notes = list(extra_notes)
    small_samples = [item.group for item in samples if 0 < item.denominator_count < 5]
    if small_samples:
        notes.append(
            f"Small-sample caution: {len(small_samples)} displayed group(s) have n < 5."
        )
    if missing:
        notes.append(f"{missing} group-run observation(s) have missing required data.")
    if excluded:
        notes.append(f"{excluded} infrastructure-failed group-run observation(s) excluded.")
    if not data:
        notes.append("No usable stored observations were available; no zero values were invented.")
    return FigureSpec(
        figure_id=figure_id,
        title=title,
        kind=kind,
        status="ready" if data else "incomplete",
        source_metric_identities=identities,
        data=tuple(data),
        samples=tuple(samples),
        notes=tuple(notes),
    )


def _validate_unique_runs(runs: Sequence[AnalysisRun]) -> None:
    run_ids = [run.run_id for run in runs]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("research input contains duplicate run IDs")
