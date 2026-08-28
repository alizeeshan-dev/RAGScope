"""Provider- and persistence-independent contracts for experiment analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

ANALYSIS_AGGREGATION_VERSION: Final = "analysis-aggregation.v1"
ANALYSIS_EXPORT_SCHEMA_VERSION: Final = "ragscope-analysis-export.v1"
EVIDENCE_SURVIVAL_METRIC_VERSION: Final = "evidence-survival.v1"

type DimensionValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class MetricObservation:
    """One stored evaluation result projected into the analysis layer."""

    name: str
    version: str
    scope: str
    method: str
    value: float | None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.version or not self.scope or not self.method:
            raise ValueError("metric identity fields cannot be empty")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("metric value must be finite when present")


@dataclass(frozen=True, slots=True)
class EvidenceSurvivalObservation:
    """Required-evidence completeness at each observable pipeline boundary."""

    retrieval: float | None
    reranking: float | None
    context: float | None
    metric_version: str = EVIDENCE_SURVIVAL_METRIC_VERSION

    def __post_init__(self) -> None:
        for stage in ("retrieval", "reranking", "context"):
            value = getattr(self, stage)
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError(f"{stage} evidence survival must be between zero and one")


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """All observable analysis inputs for one immutable query run."""

    run_id: str
    pipeline_id: str
    question_id: str
    run_status: str
    infrastructure_failure_code: str | None = None
    dimensions: dict[str, DimensionValue] = field(default_factory=dict)
    metrics: tuple[MetricObservation, ...] = ()
    evidence_survival: EvidenceSurvivalObservation | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not self.pipeline_id or not self.question_id:
            raise ValueError("run, pipeline, and question identifiers cannot be empty")

    @property
    def has_infrastructure_failure(self) -> bool:
        return self.infrastructure_failure_code is not None

    def dimension(self, name: str) -> DimensionValue:
        """Resolve standard dimensions and then caller-supplied benchmark dimensions."""

        standard: dict[str, DimensionValue] = {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "question_id": self.question_id,
            "run_status": self.run_status,
            "infrastructure_failure_code": self.infrastructure_failure_code,
        }
        return standard[name] if name in standard else self.dimensions.get(name)


@dataclass(frozen=True, slots=True)
class DenominatorPolicy:
    """Explicit eligibility rule for one aggregate metric.

    Missing metric values are always excluded from the numeric denominator and
    counted separately. ``exclude_infrastructure_failures`` controls whether
    documented infrastructure failures are removed before missingness is
    assessed. Poor answer quality is never an exclusion reason.
    """

    exclude_infrastructure_failures: bool
    description: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("denominator policy requires a description")


@dataclass(frozen=True, slots=True)
class MetricSelector:
    """Exact version/method identity and denominator policy for aggregation."""

    name: str
    version: str
    scope: str
    method: str
    denominator_policy: DenominatorPolicy

    def __post_init__(self) -> None:
        if not self.name or not self.version or not self.scope or not self.method:
            raise ValueError("metric selector identity fields cannot be empty")

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.name, self.version, self.scope, self.method)


@dataclass(frozen=True, slots=True)
class FilterCondition:
    """Exact allowed values for one standard or custom dimension."""

    dimension: str
    allowed_values: tuple[DimensionValue, ...]

    def __post_init__(self) -> None:
        if not self.dimension or not self.allowed_values:
            raise ValueError("filter condition needs a dimension and allowed values")


@dataclass(frozen=True, slots=True)
class AggregationPlan:
    """Frozen, reproducible instructions for one aggregation calculation."""

    selectors: tuple[MetricSelector, ...]
    group_by: tuple[str, ...] = ()
    filters: tuple[FilterCondition, ...] = ()
    aggregation_version: str = ANALYSIS_AGGREGATION_VERSION

    def __post_init__(self) -> None:
        if not self.selectors:
            raise ValueError("aggregation plan needs at least one metric selector")
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by dimensions must be unique")
        filter_dimensions = [condition.dimension for condition in self.filters]
        if len(set(filter_dimensions)) != len(filter_dimensions):
            raise ValueError("filter dimensions must be unique")
        identities = [selector.identity for selector in self.selectors]
        if len(set(identities)) != len(identities):
            raise ValueError("metric selectors must have unique exact identities")


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    """One group/metric aggregate with its complete denominator accounting."""

    group: tuple[tuple[str, DimensionValue], ...]
    metric_name: str
    metric_version: str
    metric_scope: str
    evaluation_method: str
    denominator_policy: DenominatorPolicy
    total_run_count: int
    denominator_count: int
    missing_metric_count: int
    excluded_infrastructure_count: int
    value_sum: float | None
    mean: float | None
    median: float | None
    contributing_run_ids: tuple[str, ...]
    missing_run_ids: tuple[str, ...]
    excluded_run_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        accounted = (
            self.denominator_count
            + self.missing_metric_count
            + self.excluded_infrastructure_count
        )
        if accounted != self.total_run_count:
            raise ValueError("aggregate denominator accounting does not equal total runs")
        if self.denominator_count != len(self.contributing_run_ids):
            raise ValueError("denominator does not match contributing run IDs")


@dataclass(frozen=True, slots=True)
class AggregationResult:
    plan: AggregationPlan
    source_run_count: int
    filtered_run_count: int
    filtered_run_ids: tuple[str, ...]
    aggregates: tuple[AggregateMetric, ...]


@dataclass(frozen=True, slots=True)
class AnalysisExportBundle:
    """Deterministic external artifacts returned without filesystem side effects."""

    schema_version: str
    run_metrics_csv: str
    aggregate_metrics_csv: str
    json_document: str
