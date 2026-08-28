from __future__ import annotations

import json
import logging

import backend.app.core.logging as structured_logging
from backend.app.analysis.contracts import AnalysisRun, MetricObservation
from backend.app.analysis.service import _headline_metrics, _visualization_data


def _analysis_run(
    run_id: str,
    *,
    pipeline: str,
    correctness: float | None,
    latency: int | None,
    infrastructure_failure: bool = False,
) -> AnalysisRun:
    metrics = (
        MetricObservation(
            name="answer_correctness",
            version="generation-quality-v1",
            scope="generation",
            method="human",
            value=correctness,
        ),
    )
    return AnalysisRun(
        run_id=run_id,
        pipeline_id=pipeline,
        question_id=f"question-{run_id}",
        run_status="failed" if infrastructure_failure else "succeeded",
        infrastructure_failure_code=("TIMEOUT" if infrastructure_failure else None),
        dimensions={
            "pipeline_name": pipeline,
            "question_type": "direct_fact_lookup",
            "total_latency_ms": latency,
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost": None,
            "failure_stage": "infrastructure" if infrastructure_failure else None,
        },
        metrics=metrics,
    )


def test_dashboard_summaries_use_the_complete_population_and_explicit_denominators() -> None:
    runs = (
        _analysis_run("one", pipeline="P1", correctness=1.0, latency=100),
        _analysis_run("two", pipeline="P1", correctness=0.0, latency=300),
        _analysis_run(
            "three",
            pipeline="P2",
            correctness=None,
            latency=None,
            infrastructure_failure=True,
        ),
    )

    headlines = _headline_metrics(runs)
    visualizations = _visualization_data(runs)

    assert headlines["answer_correctness"] == {
        "value": 0.5,
        "numerator": 1.0,
        "denominator": 2,
        "missing": 0,
        "excluded_infrastructure": 1,
    }
    assert headlines["median_latency_ms"]["value"] == 200.0
    assert headlines["median_latency_ms"]["missing"] == 1
    assert headlines["median_cost"]["value"] is None
    assert headlines["infrastructure_failure_rate"]["value"] == 1 / 3
    assert {row["pipeline"] for row in visualizations["latency_by_pipeline"]} == {
        "P1",
        "P2",
    }
    assert visualizations["failure_distribution"][0]["count"] == 1


def test_json_logging_recursively_redacts_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        structured_logging,
        "configured_sensitive_values",
        lambda: ("fixture-secret-value",),
    )
    record = logging.LogRecord(
        name="ragscope.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider.failed",
        args=(),
        exc_info=None,
    )
    record.event_fields = {
        "authorization": "Bearer fixture-secret-value",
        "nested": {"api_key": "fixture-secret-value", "safe_id": "run-1"},
    }

    payload = json.loads(structured_logging.JsonFormatter().format(record))

    serialized = json.dumps(payload)
    assert "fixture-secret-value" not in serialized
    assert payload["fields"]["authorization"] == "[REDACTED]"
    assert payload["fields"]["nested"]["api_key"] == "[REDACTED]"
    assert payload["fields"]["nested"]["safe_id"] == "run-1"
