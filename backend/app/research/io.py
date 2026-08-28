"""Validated export loading and generated-only research artifact writing."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.app.analysis.contracts import (
    ANALYSIS_EXPORT_SCHEMA_VERSION,
    AnalysisRun,
    DenominatorPolicy,
    EvidenceSurvivalObservation,
    MetricObservation,
)
from backend.app.research.contracts import (
    RESEARCH_FIGURE_SCHEMA_VERSION,
    ExactMetric,
    FigureArtifact,
    ResearchDataset,
    ResearchFigureConfiguration,
)


def load_analysis_export(path: Path) -> ResearchDataset:
    """Load only the versioned JSON export produced by the analysis layer."""

    payload = _object(json.loads(path.read_text(encoding="utf-8")), "export")
    schema_version = _string(payload.get("schema_version"), "schema_version")
    if schema_version != ANALYSIS_EXPORT_SCHEMA_VERSION:
        raise ValueError(f"unsupported analysis export schema {schema_version!r}")
    raw_runs = payload.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError("analysis export runs must be a list")
    runs = tuple(_analysis_run(_object(item, "run")) for item in raw_runs)
    run_ids = [run.run_id for run in runs]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("analysis export contains duplicate run IDs")
    return ResearchDataset(source_schema_version=schema_version, runs=runs)


def load_figure_configuration(path: Path) -> ResearchFigureConfiguration:
    """Load exact metric selectors and denominator policies from JSON."""

    payload = _object(json.loads(path.read_text(encoding="utf-8")), "configuration")
    schema = _string(payload.get("schema_version"), "schema_version")
    if schema != RESEARCH_FIGURE_SCHEMA_VERSION:
        raise ValueError(f"unsupported figure configuration schema {schema!r}")
    selectors = _object(payload.get("metric_selectors"), "metric_selectors")
    return ResearchFigureConfiguration(
        retrieval_recall=_exact_metric(selectors, "retrieval_recall"),
        answer_correctness=_exact_metric(selectors, "answer_correctness"),
        citation_support_rate=_exact_metric(selectors, "citation_support_rate"),
        estimated_cost=_exact_metric(selectors, "estimated_cost"),
        total_latency=_exact_metric(selectors, "total_latency"),
        evidence_survival_version=_string(
            payload.get("evidence_survival_version"), "evidence_survival_version"
        ),
        evidence_survival_policy=_policy(
            _object(payload.get("evidence_survival_policy"), "evidence_survival_policy")
        ),
        pipeline_label_dimension=_configuration_string(
            payload, "pipeline_label_dimension", "pipeline_name"
        ),
        question_type_dimension=_configuration_string(
            payload, "question_type_dimension", "question_type"
        ),
        failure_stage_dimension=_configuration_string(
            payload, "failure_stage_dimension", "primary_failure_stage"
        ),
        schema_version=schema,
    )


def write_figure_bundle(
    artifacts: tuple[FigureArtifact, ...],
    output_directory: Path,
    *,
    generated_root: Path,
    source_export_sha256: str | None = None,
    configuration_sha256: str | None = None,
) -> tuple[Path, ...]:
    """Write JSON/SVG only beneath an explicitly generated, gitignored root."""

    root = generated_root.resolve()
    output = output_directory.resolve()
    if output != root and root not in output.parents:
        raise ValueError("research output must be inside the configured generated root")
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest_figures: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not re.fullmatch(r"[a-z0-9_]+", artifact.spec.figure_id):
            raise ValueError("figure ID is not a safe generated filename")
        spec_path = output / f"{artifact.spec.figure_id}.json"
        svg_path = output / f"{artifact.spec.figure_id}.svg"
        spec_payload = _figure_json(artifact)
        spec_path.write_text(_canonical_json(spec_payload) + "\n", encoding="utf-8")
        svg_path.write_text(artifact.svg, encoding="utf-8")
        written.extend((spec_path, svg_path))
        manifest_figures.append(
            {
                "figure_id": artifact.spec.figure_id,
                "status": artifact.spec.status,
                "json": spec_path.name,
                "svg": svg_path.name,
            }
        )
    manifest = {
        "schema_version": RESEARCH_FIGURE_SCHEMA_VERSION,
        "source_export_sha256": source_export_sha256,
        "configuration_sha256": configuration_sha256,
        "figures": manifest_figures,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    written.append(manifest_path)
    return tuple(written)


def _analysis_run(payload: dict[str, Any]) -> AnalysisRun:
    dimensions = _object(payload.get("dimensions", {}), "dimensions")
    for key, value in dimensions.items():
        if not isinstance(key, str) or not isinstance(value, (str, int, bool, type(None))):
            raise ValueError("run dimensions must contain scalar JSON values")
    raw_metrics = payload.get("metrics", [])
    if not isinstance(raw_metrics, list):
        raise ValueError("run metrics must be a list")
    metrics = tuple(_metric(_object(item, "metric")) for item in raw_metrics)
    raw_survival = payload.get("evidence_survival")
    survival = (
        _survival(_object(raw_survival, "evidence_survival"))
        if raw_survival is not None
        else None
    )
    return AnalysisRun(
        run_id=_string(payload.get("run_id"), "run_id"),
        pipeline_id=_string(payload.get("pipeline_id"), "pipeline_id"),
        question_id=_string(payload.get("question_id"), "question_id"),
        run_status=_string(payload.get("run_status"), "run_status"),
        infrastructure_failure_code=_optional_string(
            payload.get("infrastructure_failure_code"), "infrastructure_failure_code"
        ),
        dimensions=dict(dimensions),
        metrics=metrics,
        evidence_survival=survival,
    )


def _metric(payload: dict[str, Any]) -> MetricObservation:
    value = _optional_number(payload.get("value"), "metric value")
    return MetricObservation(
        name=_string(payload.get("name"), "metric name"),
        version=_string(payload.get("version"), "metric version"),
        scope=_string(payload.get("scope"), "metric scope"),
        method=_string(payload.get("method"), "metric method"),
        value=value,
        details=_object(payload.get("details", {}), "metric details"),
    )


def _survival(payload: dict[str, Any]) -> EvidenceSurvivalObservation:
    return EvidenceSurvivalObservation(
        retrieval=_optional_number(payload.get("retrieval"), "retrieval survival"),
        reranking=_optional_number(payload.get("reranking"), "reranking survival"),
        context=_optional_number(payload.get("context"), "context survival"),
        metric_version=_string(payload.get("metric_version"), "survival metric version"),
    )


def _exact_metric(selectors: dict[str, Any], key: str) -> ExactMetric:
    payload = _object(selectors.get(key), key)
    return ExactMetric(
        name=_string(payload.get("name"), f"{key}.name"),
        version=_string(payload.get("version"), f"{key}.version"),
        scope=_string(payload.get("scope"), f"{key}.scope"),
        method=_string(payload.get("method"), f"{key}.method"),
        denominator_policy=_policy(_object(payload.get("denominator_policy"), key)),
    )


def _policy(payload: dict[str, Any]) -> DenominatorPolicy:
    excluded = payload.get("exclude_infrastructure_failures")
    if not isinstance(excluded, bool):
        raise ValueError("denominator policy exclusion flag must be boolean")
    return DenominatorPolicy(
        exclude_infrastructure_failures=excluded,
        description=_string(payload.get("description"), "denominator description"),
    )


def _figure_json(artifact: FigureArtifact) -> dict[str, Any]:
    payload = asdict(artifact.spec)
    payload["source_metric_identities"] = [
        list(identity) for identity in artifact.spec.source_metric_identities
    ]
    return payload


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _optional_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric or null")
    return float(value)


def _configuration_string(payload: dict[str, Any], key: str, default: str) -> str:
    return _string(payload[key], key) if key in payload else default


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
