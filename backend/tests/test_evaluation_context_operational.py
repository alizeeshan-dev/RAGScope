from __future__ import annotations

import typing
from uuid import uuid4

import pytest
from backend.app.evaluation.metrics.context import (
    ContextItem,
    ContextMetricInputs,
    evaluate_context,
)
from backend.app.evaluation.metrics.operational import (
    OperationalMetricInputs,
    evaluate_operational,
)


def _values(outputs: typing.Sequence[typing.Any]) -> dict[str, float | None]:
    return {output.name: output.value for output in outputs}


def test_context_metrics_use_best_alternative_evidence_set() -> None:
    document = uuid4()
    required_a = uuid4()
    required_b = uuid4()
    alternative = uuid4()
    distractor = uuid4()
    outputs = evaluate_context(
        ContextMetricInputs(
            selected=(
                ContextItem(alternative, document, "one exact passage", 3),
                ContextItem(distractor, uuid4(), "unrelated passage", 2),
            ),
            acceptable_chunk_sets=(
                frozenset({required_a, required_b}),
                frozenset({alternative}),
            ),
        )
    )
    values = _values(outputs)
    assert values["context_recall"] == 1.0
    assert values["required_evidence_retained"] == 1.0
    assert values["context_precision"] == 0.5
    assert values["context_token_count"] == 5.0
    assert values["context_source_diversity"] == 1.0


def test_context_human_metrics_are_missing_without_annotation() -> None:
    outputs = evaluate_context(
        ContextMetricInputs(selected=(), acceptable_chunk_sets=None)
    )
    values = _values(outputs)
    assert values["context_precision"] is None
    assert values["context_recall"] is None
    assert values["context_token_count"] == 0.0


def test_context_redundancy_is_hand_calculated() -> None:
    document = uuid4()
    outputs = evaluate_context(
        ContextMetricInputs(
            selected=(
                ContextItem(uuid4(), document, "alpha beta gamma", 3),
                ContextItem(uuid4(), document, "alpha beta gamma", 3),
                ContextItem(uuid4(), document, "delta epsilon", 2),
            ),
            acceptable_chunk_sets=(),
        )
    )
    assert _values(outputs)["context_redundancy_rate"] == pytest.approx(1 / 3)


def test_operational_metrics_preserve_missing_cost() -> None:
    values = _values(
        evaluate_operational(
            OperationalMetricInputs(
                total_latency_ms=123,
                stage_latency_ms={"retrieval": 20},
                input_tokens=10,
                output_tokens=4,
                estimated_cost=None,
                retrieval_calls=2,
                model_calls=1,
            )
        )
    )
    assert values["total_latency_ms"] == 123.0
    assert values["stage_latency_ms.retrieval"] == 20.0
    assert values["estimated_cost"] is None
    assert values["failure_rate"] == 0.0
