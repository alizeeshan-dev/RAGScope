"""Deterministic tidy CSV and versioned JSON analysis exports."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from io import StringIO
from typing import Any

from backend.app.analysis.contracts import (
    ANALYSIS_EXPORT_SCHEMA_VERSION,
    AggregateMetric,
    AggregationResult,
    AnalysisExportBundle,
    AnalysisRun,
    MetricObservation,
)


def build_export_bundle(
    runs: Sequence[AnalysisRun], result: AggregationResult
) -> AnalysisExportBundle:
    """Return deterministic raw/aggregate CSV plus a reproducible JSON document."""

    return AnalysisExportBundle(
        schema_version=ANALYSIS_EXPORT_SCHEMA_VERSION,
        run_metrics_csv=export_run_metrics_csv(runs),
        aggregate_metrics_csv=export_aggregate_metrics_csv(result),
        json_document=export_versioned_json(runs, result),
    )


def export_run_metrics_csv(runs: Sequence[AnalysisRun]) -> str:
    """Export one tidy row per run/metric observation, preserving null as blank."""

    columns = (
        "export_schema_version",
        "run_id",
        "pipeline_id",
        "question_id",
        "run_status",
        "infrastructure_failure_code",
        "dimensions_json",
        "metric_name",
        "metric_version",
        "metric_scope",
        "evaluation_method",
        "metric_value",
        "metric_details_json",
    )
    rows: list[dict[str, object]] = []
    for run in sorted(runs, key=lambda item: item.run_id):
        for metric in sorted(runs_metrics(run), key=_metric_key):
            rows.append(
                {
                    "export_schema_version": ANALYSIS_EXPORT_SCHEMA_VERSION,
                    "run_id": run.run_id,
                    "pipeline_id": run.pipeline_id,
                    "question_id": run.question_id,
                    "run_status": run.run_status,
                    "infrastructure_failure_code": run.infrastructure_failure_code,
                    "dimensions_json": _canonical_json(run.dimensions),
                    "metric_name": metric.name,
                    "metric_version": metric.version,
                    "metric_scope": metric.scope,
                    "evaluation_method": metric.method,
                    "metric_value": metric.value,
                    "metric_details_json": _canonical_json(metric.details),
                }
            )
    return _write_csv(columns, rows)


def export_aggregate_metrics_csv(result: AggregationResult) -> str:
    """Export one tidy row per group/metric aggregate with denominator audit fields."""

    columns = (
        "export_schema_version",
        "aggregation_version",
        "group_json",
        "metric_name",
        "metric_version",
        "metric_scope",
        "evaluation_method",
        "denominator_policy",
        "exclude_infrastructure_failures",
        "total_run_count",
        "denominator_count",
        "missing_metric_count",
        "excluded_infrastructure_count",
        "value_sum",
        "mean",
        "median",
        "contributing_run_ids_json",
        "missing_run_ids_json",
        "excluded_run_ids_json",
    )
    rows: list[dict[str, object]] = [
        {
            "export_schema_version": ANALYSIS_EXPORT_SCHEMA_VERSION,
            "aggregation_version": result.plan.aggregation_version,
            "group_json": _canonical_json(dict(item.group)),
            "metric_name": item.metric_name,
            "metric_version": item.metric_version,
            "metric_scope": item.metric_scope,
            "evaluation_method": item.evaluation_method,
            "denominator_policy": item.denominator_policy.description,
            "exclude_infrastructure_failures": (
                item.denominator_policy.exclude_infrastructure_failures
            ),
            "total_run_count": item.total_run_count,
            "denominator_count": item.denominator_count,
            "missing_metric_count": item.missing_metric_count,
            "excluded_infrastructure_count": item.excluded_infrastructure_count,
            "value_sum": item.value_sum,
            "mean": item.mean,
            "median": item.median,
            "contributing_run_ids_json": _canonical_json(item.contributing_run_ids),
            "missing_run_ids_json": _canonical_json(item.missing_run_ids),
            "excluded_run_ids_json": _canonical_json(item.excluded_run_ids),
        }
        for item in result.aggregates
    ]
    return _write_csv(columns, rows)


def export_versioned_json(
    runs: Sequence[AnalysisRun], result: AggregationResult
) -> str:
    """Export exact inputs, plan, and aggregates using canonical JSON ordering."""

    document = {
        "schema_version": ANALYSIS_EXPORT_SCHEMA_VERSION,
        "aggregation_version": result.plan.aggregation_version,
        "plan": {
            "group_by": list(result.plan.group_by),
            "filters": [
                {
                    "dimension": item.dimension,
                    "allowed_values": list(item.allowed_values),
                }
                for item in result.plan.filters
            ],
            "selectors": [
                {
                    "name": item.name,
                    "version": item.version,
                    "scope": item.scope,
                    "method": item.method,
                    "denominator_policy": {
                        "exclude_infrastructure_failures": (
                            item.denominator_policy.exclude_infrastructure_failures
                        ),
                        "description": item.denominator_policy.description,
                        "missing_values": "excluded_and_counted",
                    },
                }
                for item in result.plan.selectors
            ],
        },
        "source_run_count": result.source_run_count,
        "filtered_run_count": result.filtered_run_count,
        "filtered_run_ids": list(result.filtered_run_ids),
        "runs": [_run_json(run) for run in sorted(runs, key=lambda item: item.run_id)],
        "aggregates": [_aggregate_json(item) for item in result.aggregates],
    }
    return _canonical_json(document)


def runs_metrics(run: AnalysisRun) -> tuple[MetricObservation, ...]:
    """Small seam for integrations that project stored result rows before export."""

    return run.metrics


def _run_json(run: AnalysisRun) -> dict[str, Any]:
    survival = run.evidence_survival
    return {
        "run_id": run.run_id,
        "pipeline_id": run.pipeline_id,
        "question_id": run.question_id,
        "run_status": run.run_status,
        "infrastructure_failure_code": run.infrastructure_failure_code,
        "dimensions": run.dimensions,
        "metrics": [
            {
                "name": metric.name,
                "version": metric.version,
                "scope": metric.scope,
                "method": metric.method,
                "value": metric.value,
                "details": metric.details,
            }
            for metric in sorted(run.metrics, key=_metric_key)
        ],
        "evidence_survival": (
            {
                "metric_version": survival.metric_version,
                "retrieval": survival.retrieval,
                "reranking": survival.reranking,
                "context": survival.context,
            }
            if survival is not None
            else None
        ),
    }


def _aggregate_json(item: AggregateMetric) -> dict[str, Any]:
    return {
        "group": dict(item.group),
        "metric_name": item.metric_name,
        "metric_version": item.metric_version,
        "metric_scope": item.metric_scope,
        "evaluation_method": item.evaluation_method,
        "denominator_policy": {
            "exclude_infrastructure_failures": (
                item.denominator_policy.exclude_infrastructure_failures
            ),
            "description": item.denominator_policy.description,
            "missing_values": "excluded_and_counted",
        },
        "total_run_count": item.total_run_count,
        "denominator_count": item.denominator_count,
        "missing_metric_count": item.missing_metric_count,
        "excluded_infrastructure_count": item.excluded_infrastructure_count,
        "value_sum": item.value_sum,
        "mean": item.mean,
        "median": item.median,
        "contributing_run_ids": list(item.contributing_run_ids),
        "missing_run_ids": list(item.missing_run_ids),
        "excluded_run_ids": list(item.excluded_run_ids),
    }


def _write_csv(columns: tuple[str, ...], rows: list[dict[str, object]]) -> str:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _metric_key(metric: MetricObservation) -> tuple[str, str, str, str]:
    return (metric.name, metric.version, metric.scope, metric.method)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
