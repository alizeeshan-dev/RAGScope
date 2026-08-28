from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.db.models import (
    ExperimentAttemptStatus,
    ExperimentCellStatus,
    ExperimentStatus,
    PipelineExecutionMode,
    RetrievalMode,
)
from backend.app.query_runtime.schemas import QueryRunCreate


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=2, ge=1, le=10)
    retry_mode: Literal["on_resume"] = "on_resume"
    retryable_failure_codes: tuple[str, ...] = (
        "MODEL_PROVIDER_FAILURE",
        "EMBEDDING_PROVIDER_FAILURE",
        "DATABASE_FAILURE",
        "TIMEOUT",
        "EXPERIMENT_RUNNER_FAILURE",
        "EXPERIMENT_INTERRUPTED",
    )

    @model_validator(mode="after")
    def unique_failure_codes(self) -> RetryPolicy:
        if len(set(self.retryable_failure_codes)) != len(self.retryable_failure_codes):
            raise ValueError("retryable_failure_codes cannot contain duplicates")
        return self


class QuestionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    question_text: str = Field(min_length=1, max_length=10_000)
    annotation_status: str
    question_type: str | None = None
    difficulty: str | None = None
    expected_answerability: str | None = None
    reference_answer: str | None = None
    answer_criteria: str | None = None


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(default="USD", min_length=3, max_length=3)
    generation_billable: bool = True
    generation_input_per_million_tokens: float | None = Field(default=None, ge=0)
    generation_output_per_million_tokens: float | None = Field(default=None, ge=0)
    embedding_billable: bool = True
    embedding_input_per_million_tokens: float | None = Field(default=None, ge=0)
    reranker_billable: bool = False
    reranker_per_call: float | None = Field(default=None, ge=0)


class PipelineDependencySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    name: str
    version: int = Field(ge=1)
    frozen: bool
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_snapshot: dict[str, Any] = Field(default_factory=dict)
    index_snapshots: tuple[dict[str, Any], ...] = ()
    prompt_snapshot: dict[str, Any] = Field(default_factory=dict)
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.FIXED
    generation_provider: str
    generation_model: str
    retrieval_mode: RetrievalMode
    reranking_enabled: bool
    maximum_context_tokens: int = Field(ge=1)
    maximum_output_tokens: int = Field(ge=1)
    prompt_overhead_tokens: int = Field(default=256, ge=0)
    pricing: PricingSnapshot = Field(default_factory=PricingSnapshot)
    adaptive_router_configuration_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    adaptive_router_frozen: bool | None = None
    providers_available: bool = True
    unavailable_capabilities: tuple[str, ...] = ()


class ExperimentDependencySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_version_id: UUID
    corpus_status: str
    corpus_frozen: bool
    corpus_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_version_id: UUID
    benchmark_corpus_version_id: UUID
    benchmark_status: str
    benchmark_frozen: bool
    benchmark_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    questions: tuple[QuestionSnapshot, ...]
    pipelines: tuple[PipelineDependencySnapshot, ...]
    metric_versions: dict[str, str] = Field(default_factory=dict)
    citation_verifier_version: str = "citation-lexical-verifier-v1"
    failure_taxonomy_version: str = "ragscope-failure-taxonomy.v1"
    attribution_rules_version: str = "observable-attribution-rules.v1"


class ExperimentQualityMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "answer_correctness"
    version: str = "generation-quality-v1"
    scope: str = "generation"
    method: str = "human"
    higher_is_better: bool = True


class ExperimentAnalysisConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adaptive_pipeline_id: UUID | None = None
    fixed_reference_pipeline_id: UUID | None = None
    best_observed_fixed_pipeline_ids: tuple[UUID, ...] = ()
    quality_metric: ExperimentQualityMetric = Field(
        default_factory=ExperimentQualityMetric
    )
    tie_breakers: tuple[Literal["estimated_cost", "latency_ms", "route_identifier"], ...] = (
        "estimated_cost",
        "latency_ms",
        "route_identifier",
    )

    @model_validator(mode="after")
    def unique_candidates(self) -> ExperimentAnalysisConfiguration:
        if len(set(self.best_observed_fixed_pipeline_ids)) != len(
            self.best_observed_fixed_pipeline_ids
        ):
            raise ValueError("best_observed_fixed_pipeline_ids cannot contain duplicates")
        return self


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=255)
    research_question: str = Field(min_length=1, max_length=5_000)
    corpus_version_id: UUID
    benchmark_version_id: UUID
    pipeline_configuration_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    repetitions: int = Field(default=1, ge=1, le=20)
    code_commit: str = Field(min_length=7, max_length=100)
    stop_on_error: bool = False
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    analysis_configuration: ExperimentAnalysisConfiguration = Field(
        default_factory=lambda: ExperimentAnalysisConfiguration()
    )

    @model_validator(mode="after")
    def unique_pipelines(self) -> ExperimentCreate:
        if len(set(self.pipeline_configuration_ids)) != len(
            self.pipeline_configuration_ids
        ):
            raise ValueError("pipeline_configuration_ids cannot contain duplicates")
        return self


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    research_question: str
    corpus_version_id: UUID
    benchmark_version_id: UUID
    pipeline_configuration_ids: tuple[UUID, ...]
    repetitions: int
    code_commit: str
    stop_on_error: bool
    retry_policy: RetryPolicy
    analysis_configuration: ExperimentAnalysisConfiguration = Field(
        default_factory=lambda: ExperimentAnalysisConfiguration()
    )
    status: ExperimentStatus = ExperimentStatus.DRAFT
    dependency_snapshot: ExperimentDependencySnapshot
    configuration_hash: str | None = None
    created_at: datetime
    frozen_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExperimentRunCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    experiment_id: UUID
    benchmark_question_id: UUID
    pipeline_configuration_id: UUID
    repetition_index: int = Field(ge=1)
    query_text: str
    status: ExperimentCellStatus = ExperimentCellStatus.PLANNED
    idempotency_key: str
    random_seed: int
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(ge=1)
    query_run_id: UUID | None = None
    last_failure_code: str | None = None
    last_failure_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def query_request(self, experiment: ExperimentRecord) -> QueryRunCreate:
        return QueryRunCreate(
            corpus_version_id=experiment.corpus_version_id,
            pipeline_configuration_id=self.pipeline_configuration_id,
            benchmark_question_id=self.benchmark_question_id,
            query_text=self.query_text,
        )


class ExperimentRunAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_run_id: UUID
    attempt_number: int = Field(ge=1)
    status: ExperimentAttemptStatus
    query_run_id: UUID | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class QueryExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_run_id: UUID | None = None
    succeeded: bool
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def successful_run_has_identity(self) -> QueryExecutionOutcome:
        if self.succeeded and self.query_run_id is None:
            raise ValueError("a successful query outcome requires query_run_id")
        return self


class ExperimentProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int
    planned: int
    running: int
    retryable: int
    succeeded: int
    failed: int


class PipelineCostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline_configuration_id: UUID
    run_count: int
    maximum_generation_input_tokens: int
    maximum_generation_output_tokens: int
    maximum_embedding_input_tokens: int
    maximum_reranker_calls: int
    generation_cost: float | None
    embedding_cost: float | None
    reranker_cost: float | None
    maximum_expected_cost: float | None
    currency: str
    missing_pricing: tuple[str, ...] = ()


class ExperimentCostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: UUID
    run_count: int
    per_pipeline: tuple[PipelineCostEstimate, ...]
    maximum_expected_cost: float | None
    currency: str | None
    cost_fully_configured: bool


class ExperimentExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment: ExperimentRecord
    progress: ExperimentProgress
    executed_attempts: int
