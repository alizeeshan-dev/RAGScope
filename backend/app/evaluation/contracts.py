from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class MetricScope(StrEnum):
    PARSING = "parsing"
    RETRIEVAL = "retrieval"
    CONTEXT = "context"
    GENERATION = "generation"
    CITATION = "citation"
    COST = "cost"
    OVERALL = "overall"


class EvaluationMethod(StrEnum):
    AUTOMATED = "automated"
    DETERMINISTIC = "deterministic"
    HUMAN = "human"
    MODEL_JUDGE = "model_judge"
    OPERATIONAL = "operational"


@dataclass(frozen=True, slots=True)
class MetricOutput:
    """One metric value plus enough metadata to reproduce its calculation.

    ``value=None`` means the required annotation or observable input was absent.
    It must never be coerced to zero by a metric implementation or persistence.
    """

    name: str
    version: str
    scope: MetricScope
    value: float | None
    method: EvaluationMethod | str
    details: dict[str, Any] = field(default_factory=dict)


class Metric(Protocol):
    name: str
    version: str
    scope: MetricScope
    required_inputs: tuple[str, ...]
    method: EvaluationMethod | str

    def evaluate(self, inputs: object) -> MetricOutput: ...

