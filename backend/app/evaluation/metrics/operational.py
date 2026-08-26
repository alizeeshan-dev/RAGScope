from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.evaluation.contracts import EvaluationMethod, MetricOutput, MetricScope

OPERATIONAL_METRIC_VERSION = "operational-metrics.v1"


@dataclass(frozen=True, slots=True)
class OperationalMetricInputs:
    total_latency_ms: int | None
    stage_latency_ms: dict[str, int] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    retrieval_calls: int = 0
    reranking_calls: int = 0
    model_calls: int = 0
    failed: bool = False
    failure_code: str | None = None


def evaluate_operational(inputs: OperationalMetricInputs) -> list[MetricOutput]:
    """Expose existing QueryRun/Trace measurements without redefining their units."""

    outputs = [
        _metric("total_latency_ms", _float(inputs.total_latency_ms), MetricScope.OVERALL),
        _metric("input_tokens", _float(inputs.input_tokens), MetricScope.COST),
        _metric("output_tokens", _float(inputs.output_tokens), MetricScope.COST),
        _metric("estimated_cost", inputs.estimated_cost, MetricScope.COST),
        _metric("retrieval_calls", float(inputs.retrieval_calls), MetricScope.COST),
        _metric("reranking_calls", float(inputs.reranking_calls), MetricScope.COST),
        _metric("model_calls", float(inputs.model_calls), MetricScope.COST),
        _metric(
            "failure_rate",
            1.0 if inputs.failed else 0.0,
            MetricScope.OVERALL,
            {"failure_code": inputs.failure_code},
        ),
    ]
    outputs.extend(
        _metric(
            f"stage_latency_ms.{stage}",
            float(latency),
            MetricScope.OVERALL,
            {"stage": stage},
        )
        for stage, latency in sorted(inputs.stage_latency_ms.items())
    )
    return outputs


def _metric(
    name: str,
    value: float | None,
    scope: MetricScope,
    details: dict[str, object] | None = None,
) -> MetricOutput:
    return MetricOutput(
        name=name,
        version=OPERATIONAL_METRIC_VERSION,
        scope=scope,
        value=value,
        method=EvaluationMethod.OPERATIONAL,
        details=details or {},
    )


def _float(value: int | None) -> float | None:
    return float(value) if value is not None else None

