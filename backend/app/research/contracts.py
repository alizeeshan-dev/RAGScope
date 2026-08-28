"""Versioned contracts for reproducible research figure generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from backend.app.analysis.contracts import AnalysisRun, DenominatorPolicy

RESEARCH_FIGURE_SCHEMA_VERSION: Final = "ragscope-research-figures.v1"

FigureStatus = Literal["ready", "incomplete"]
FigureKind = Literal["bar", "scatter", "distribution", "grouped_bar", "line"]


@dataclass(frozen=True, slots=True)
class ExactMetric:
    """An exact stored metric identity; no name-only substitution is permitted."""

    name: str
    version: str
    scope: str
    method: str
    denominator_policy: DenominatorPolicy

    def __post_init__(self) -> None:
        if not all((self.name, self.version, self.scope, self.method)):
            raise ValueError("exact metric identity fields cannot be empty")

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.name, self.version, self.scope, self.method)


@dataclass(frozen=True, slots=True)
class ResearchFigureConfiguration:
    """Frozen selectors and dimension names used by all eight required figures."""

    retrieval_recall: ExactMetric
    answer_correctness: ExactMetric
    citation_support_rate: ExactMetric
    estimated_cost: ExactMetric
    total_latency: ExactMetric
    evidence_survival_version: str
    evidence_survival_policy: DenominatorPolicy
    pipeline_label_dimension: str = "pipeline_name"
    question_type_dimension: str = "question_type"
    failure_stage_dimension: str = "primary_failure_stage"
    schema_version: str = RESEARCH_FIGURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RESEARCH_FIGURE_SCHEMA_VERSION:
            raise ValueError("unsupported research figure configuration version")
        if not self.evidence_survival_version:
            raise ValueError("evidence survival version cannot be empty")


@dataclass(frozen=True, slots=True)
class FigureDatum:
    """One plotted value; absent values are omitted rather than encoded as zero."""

    series: str
    category: str | None = None
    value: float | None = None
    x: float | None = None
    y: float | None = None
    count: int | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class SampleAudit:
    """Auditable denominator accounting for one plotted group."""

    group: str
    total_run_count: int
    denominator_count: int
    missing_count: int
    excluded_infrastructure_count: int
    contributing_run_ids: tuple[str, ...]
    missing_run_ids: tuple[str, ...]
    excluded_run_ids: tuple[str, ...]
    denominator_description: str

    def __post_init__(self) -> None:
        accounted = (
            self.denominator_count
            + self.missing_count
            + self.excluded_infrastructure_count
        )
        if accounted != self.total_run_count:
            raise ValueError("sample audit counts do not equal total runs")
        if self.denominator_count != len(self.contributing_run_ids):
            raise ValueError("sample denominator does not match contributing run IDs")


@dataclass(frozen=True, slots=True)
class FigureSpec:
    """Scientific data behind one SVG, including exact provenance and denominators."""

    figure_id: str
    title: str
    kind: FigureKind
    status: FigureStatus
    source_metric_identities: tuple[tuple[str, str, str, str], ...]
    data: tuple[FigureDatum, ...]
    samples: tuple[SampleAudit, ...]
    notes: tuple[str, ...] = ()
    schema_version: str = RESEARCH_FIGURE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class FigureArtifact:
    spec: FigureSpec
    svg: str


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    """Validated stored/exported runs plus their source schema identity."""

    source_schema_version: str
    runs: tuple[AnalysisRun, ...]
