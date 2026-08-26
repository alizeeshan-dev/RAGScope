from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from backend.app.evaluation.contracts import (
    EvaluationMethod,
    MetricOutput,
    MetricScope,
)

CONTEXT_METRIC_VERSION = "context-metrics.v1"


@dataclass(frozen=True, slots=True)
class ContextItem:
    chunk_id: UUID
    document_id: UUID
    text: str
    token_count: int


@dataclass(frozen=True, slots=True)
class ContextMetricInputs:
    selected: tuple[ContextItem, ...]
    acceptable_chunk_sets: tuple[frozenset[UUID], ...] | None
    redundancy_threshold: float = 0.85


def evaluate_context(inputs: ContextMetricInputs) -> list[MetricOutput]:
    """Evaluate exact selected context against human evidence and observable text.

    Human-grounded precision uses the union of acceptable evidence, while recall
    uses the best-covered acceptable set. Thus selecting one complete valid set is
    not penalized because another valid alternative was not selected.
    """

    selected_ids = {item.chunk_id for item in inputs.selected}
    ground_truth = inputs.acceptable_chunk_sets
    precision: float | None = None
    recall: float | None = None
    best_set_number: int | None = None
    best_overlap = 0
    if ground_truth is not None:
        relevant = set().union(*ground_truth) if ground_truth else set()
        precision = (
            len(selected_ids & relevant) / len(selected_ids)
            if selected_ids
            else 0.0
        )
        if ground_truth:
            coverage = [
                (len(selected_ids & required) / len(required) if required else 1.0)
                for required in ground_truth
            ]
            recall = max(coverage)
            best_set_number = coverage.index(recall) + 1
            best_overlap = len(selected_ids & ground_truth[best_set_number - 1])

    details: dict[str, Any] = {
        "selected_chunk_ids": sorted(str(value) for value in selected_ids),
        "acceptable_evidence_sets": (
            [sorted(str(value) for value in item) for item in ground_truth]
            if ground_truth is not None
            else None
        ),
        "best_evidence_set_number": best_set_number,
        "best_evidence_overlap": best_overlap,
    }
    redundancy = _redundancy_rate(inputs.selected, inputs.redundancy_threshold)
    diversity = (
        len({item.document_id for item in inputs.selected}) / len(inputs.selected)
        if inputs.selected
        else 0.0
    )
    token_count = sum(item.token_count for item in inputs.selected)
    return [
        _output("context_precision", precision, details),
        _output("context_recall", recall, details),
        _output("required_evidence_retained", recall, details),
        _output(
            "context_redundancy_rate",
            redundancy,
            {"threshold": inputs.redundancy_threshold, "selected_count": len(inputs.selected)},
        ),
        _output(
            "context_source_diversity",
            diversity,
            {
                "unique_document_count": len({item.document_id for item in inputs.selected}),
                "selected_count": len(inputs.selected),
            },
        ),
        _output(
            "context_token_count",
            float(token_count),
            {"selected_count": len(inputs.selected)},
        ),
    ]


def _output(name: str, value: float | None, details: dict[str, Any]) -> MetricOutput:
    return MetricOutput(
        name=name,
        version=CONTEXT_METRIC_VERSION,
        scope=MetricScope.CONTEXT,
        value=value,
        method=EvaluationMethod.DETERMINISTIC,
        details=details,
    )


def _redundancy_rate(items: Iterable[ContextItem], threshold: float) -> float:
    seen: list[frozenset[str]] = []
    redundant = 0
    count = 0
    for item in items:
        count += 1
        tokens = frozenset(item.text.casefold().split())
        if any(_jaccard(tokens, previous) >= threshold for previous in seen):
            redundant += 1
        seen.append(tokens)
    return redundant / count if count else 0.0


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0
