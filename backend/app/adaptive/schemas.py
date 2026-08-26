from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.db.models import RetrievalMode


class QueryCategory(StrEnum):
    DIRECT_FACT = "direct_fact"
    DATASET_LOOKUP = "dataset_lookup"
    COMPARISON = "comparison"
    MULTI_DOCUMENT_SYNTHESIS = "multi_document_synthesis"
    MULTI_HOP_RELATIONSHIP = "multi_hop_relationship"
    BROAD_EXPLORATORY = "broad_exploratory"
    METADATA_FILTER = "metadata_filter"
    POTENTIALLY_UNANSWERABLE = "potentially_unanswerable"


class ConfidenceLabel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClassifierConfiguration(BaseModel):
    """Frozen-by-value settings used by the deterministic query classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classifier_id: str = Field(default="rule-query-classifier", min_length=1, max_length=100)
    version: str = Field(default="1.0.0", min_length=1, max_length=50)
    broad_query_token_threshold: int = Field(default=18, ge=3, le=500)


class QueryClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: QueryCategory
    reason_code: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    confidence: ConfidenceLabel
    classifier_id: str
    classifier_version: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RouterConfiguration(BaseModel):
    """Versioned rule settings; persistence can freeze this exact snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    router_id: str = Field(default="deterministic-rule-router", min_length=1, max_length=100)
    version: str = Field(default="1.0.0", min_length=1, max_length=50)
    fallback_policy: Literal["reject"] = "reject"
    trivial_query_max_tokens: int = Field(default=2, ge=0, le=20)
    rewrite_token_threshold: int = Field(default=18, ge=1, le=500)
    standard_candidate_count: int = Field(default=20, ge=1, le=200)
    complex_candidate_count: int = Field(default=40, ge=1, le=200)
    standard_context_budget: int = Field(default=2_048, ge=1, le=100_000)
    broad_context_budget: int = Field(default=4_096, ge=1, le=100_000)
    complex_context_budget: int = Field(default=8_192, ge=1, le=100_000)
    enable_complex_reranking: bool = True
    enable_complex_rewriting: bool = True
    allowed_retrieval_modes: tuple[RetrievalMode, ...] = (
        RetrievalMode.NONE,
        RetrievalMode.LEXICAL,
        RetrievalMode.DENSE,
        RetrievalMode.HYBRID,
    )

    @model_validator(mode="after")
    def validate_scaled_settings(self) -> RouterConfiguration:
        if not self.allowed_retrieval_modes:
            raise ValueError("at least one retrieval mode must be allowed")
        if len(set(self.allowed_retrieval_modes)) != len(self.allowed_retrieval_modes):
            raise ValueError("allowed_retrieval_modes cannot contain duplicates")
        if self.complex_candidate_count < self.standard_candidate_count:
            raise ValueError("complex_candidate_count must cover standard_candidate_count")
        if self.broad_context_budget < self.standard_context_budget:
            raise ValueError("broad_context_budget must cover standard_context_budget")
        if self.complex_context_budget < self.broad_context_budget:
            raise ValueError("complex_context_budget must cover broad_context_budget")
        return self


class RouterConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)
    configuration: RouterConfiguration = Field(default_factory=RouterConfiguration)


class RouterConfigurationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: int
    router_type: str
    router_version: str
    classifier_version: str
    configuration: dict[str, object]
    configuration_hash: str
    created_at: datetime
    frozen_at: datetime | None


class CorpusCapabilities(BaseModel):
    """Observable route availability for one corpus/runtime combination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical_available: bool = False
    dense_available: bool = False
    reranker_available: bool = False
    rewriting_available: bool = True
    maximum_candidate_count: int = Field(default=200, ge=1, le=10_000)
    maximum_context_budget: int = Field(default=100_000, ge=1, le=1_000_000)

    def supports(self, mode: RetrievalMode) -> bool:
        if mode is RetrievalMode.NONE:
            return True
        if mode is RetrievalMode.LEXICAL:
            return self.lexical_available
        if mode is RetrievalMode.DENSE:
            return self.dense_available
        return self.lexical_available and self.dense_available


class RouterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=10_000)
    classification: QueryClassification
    metadata_filters: dict[str, str | int | list[str] | list[int]] = Field(default_factory=dict)
    quoted_phrase_count: int = Field(default=0, ge=0)
    identifier_count: int = Field(default=0, ge=0)
    capabilities: CorpusCapabilities


class RouteReasonCode(StrEnum):
    TRIVIAL_QUERY_NO_RETRIEVAL = "TRIVIAL_QUERY_NO_RETRIEVAL"
    METADATA_OR_EXACT_MATCH = "METADATA_OR_EXACT_MATCH"
    SEMANTIC_LOOKUP = "SEMANTIC_LOOKUP"
    BROAD_SEMANTIC_SEARCH = "BROAD_SEMANTIC_SEARCH"
    COMPLEX_EVIDENCE_SEARCH = "COMPLEX_EVIDENCE_SEARCH"


class AdaptiveRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_mode: RetrievalMode
    rewriting_enabled: bool
    reranking_enabled: bool
    candidate_count: int = Field(ge=0, le=10_000)
    context_budget: int = Field(ge=1, le=1_000_000)
    reason_code: RouteReasonCode
    reason: str = Field(min_length=1, max_length=500)
    router_id: str
    router_version: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    classifier_version: str


class BestObservedCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criteria_id: str = "best-observed-quality-cost-latency"
    version: str = "1.0.0"
    quality_metric_name: str = "overall_quality"
    higher_quality_is_better: bool = True
    minimum_quality: float | None = None
    exclude_infrastructure_failures: bool = True
    tie_breakers: tuple[Literal["estimated_cost", "latency_ms", "route_identifier"], ...] = (
        "estimated_cost",
        "latency_ms",
        "route_identifier",
    )


class RunObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route_identifier: str
    quality_value: float | None = None
    retrieval_calls: int | None = Field(default=None, ge=0)
    reranking_calls: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    infrastructure_failure: bool = False


class AdaptiveComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adaptive_route_identifier: str
    fixed_route_identifier: str
    best_observed_route_identifier: str | None
    best_observed_criteria: BestObservedCriteria
    route_accuracy_against_best_observed: float | None
    answer_quality_delta: float | None
    quality_loss: float | None
    retrieval_calls_avoided: int | None
    reranking_calls_avoided: int | None
    token_reduction: int | None
    estimated_cost_reduction: float | None
    latency_reduction_ms: int | None
