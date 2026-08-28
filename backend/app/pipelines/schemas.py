from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.db.models import PipelineExecutionMode, RetrievalMode


class LexicalConfiguration(BaseModel):
    top_k: int = Field(default=10, ge=1, le=100)
    candidate_count: int = Field(default=20, ge=1, le=200)

    @model_validator(mode="after")
    def candidate_count_covers_top_k(self) -> LexicalConfiguration:
        if self.candidate_count < self.top_k:
            raise ValueError("candidate_count must be greater than or equal to top_k")
        return self


class DenseConfiguration(LexicalConfiguration):
    similarity_method: Literal["cosine"] = "cosine"


class FusionConfiguration(BaseModel):
    method: Literal["rrf"] = "rrf"
    rrf_k: int = Field(default=60, ge=1, le=10_000)
    lexical_weight: float = Field(default=1.0, gt=0, le=100)
    dense_weight: float = Field(default=1.0, gt=0, le=100)
    final_count: int = Field(default=10, ge=1, le=100)


class RerankerConfiguration(BaseModel):
    enabled: bool = False
    provider: Literal["fake", "sentence_transformers_cross_encoder"] = "fake"
    model: str = Field(default="fake-token-overlap-reranker-v1", min_length=1, max_length=255)
    model_revision: str | None = Field(default=None, min_length=1, max_length=255)
    batch_size: int = Field(default=16, ge=1, le=1024)
    device: str | None = Field(default=None, min_length=1, max_length=100)
    local_files_only: bool = True
    input_candidate_count: int = Field(default=20, ge=1, le=200)
    final_count: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def final_count_fits_candidates(self) -> RerankerConfiguration:
        if self.final_count > self.input_candidate_count:
            raise ValueError("reranker final_count cannot exceed input_candidate_count")
        return self


class QueryProcessingConfiguration(BaseModel):
    classification_enabled: bool = True
    rewriting_enabled: bool = False
    rewriting_strategy: Literal["normalize-only", "deterministic-keywords"] = "normalize-only"


class ContextConfiguration(BaseModel):
    token_budget: int = Field(default=2048, ge=1, le=100_000)
    deduplicate: bool = True
    overlap_threshold: float = Field(default=0.85, ge=0, le=1)


class GenerationConfiguration(BaseModel):
    provider: Literal[
        "fake", "gemini", "openai_compatible", "openai-compatible"
    ] = "fake"
    model: str = Field(default="fake-generation-v1", min_length=1, max_length=255)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=512, ge=1, le=32_000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    input_price_per_million_tokens: float | None = Field(default=None, ge=0)
    output_price_per_million_tokens: float | None = Field(default=None, ge=0)
    fake_answerability: Literal[
        "auto", "answerable", "partially_answerable", "unanswerable"
    ] = "auto"


class CitationConfiguration(BaseModel):
    verification_method: Literal["citation-existence-v1"] = "citation-existence-v1"
    require_factual_claim_citations: bool = True


class PromptReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str = Field(min_length=1, max_length=255)
    version: int = Field(ge=1)


class PromptVersionsConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounded_generation: PromptReference = Field(
        default_factory=lambda: PromptReference(prompt_id="grounded-answer", version=1)
    )


class AdaptivePipelineConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_retrieval_modes: tuple[RetrievalMode, ...] = (
        RetrievalMode.NONE,
        RetrievalMode.LEXICAL,
        RetrievalMode.DENSE,
        RetrievalMode.HYBRID,
    )
    allow_rewriting: bool = True
    allow_reranking: bool = True
    maximum_candidate_count: int = Field(default=200, ge=1, le=10_000)
    maximum_context_budget: int = Field(default=100_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_modes(self) -> AdaptivePipelineConfiguration:
        if not self.allowed_retrieval_modes:
            raise ValueError("adaptive pipelines require at least one allowed retrieval mode")
        if len(set(self.allowed_retrieval_modes)) != len(self.allowed_retrieval_modes):
            raise ValueError("adaptive allowed retrieval modes cannot contain duplicates")
        return self


class PipelineConfigurationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.FIXED
    router_configuration_id: UUID | None = None
    adaptive_configuration: AdaptivePipelineConfiguration = Field(
        default_factory=AdaptivePipelineConfiguration
    )
    retrieval_mode: RetrievalMode
    lexical_configuration: LexicalConfiguration = Field(default_factory=LexicalConfiguration)
    dense_configuration: DenseConfiguration = Field(default_factory=DenseConfiguration)
    fusion_configuration: FusionConfiguration = Field(default_factory=FusionConfiguration)
    reranker_configuration: RerankerConfiguration = Field(
        default_factory=RerankerConfiguration
    )
    query_processing_configuration: QueryProcessingConfiguration = Field(
        default_factory=QueryProcessingConfiguration
    )
    context_configuration: ContextConfiguration = Field(default_factory=ContextConfiguration)
    generation_configuration: GenerationConfiguration = Field(
        default_factory=GenerationConfiguration
    )
    citation_configuration: CitationConfiguration = Field(
        default_factory=CitationConfiguration
    )
    prompt_versions: PromptVersionsConfiguration = Field(
        default_factory=PromptVersionsConfiguration
    )

    @model_validator(mode="after")
    def validate_mode_combinations(self) -> PipelineConfigurationCreate:
        if self.execution_mode is PipelineExecutionMode.ADAPTIVE:
            if self.router_configuration_id is None:
                raise ValueError("adaptive pipelines require a router configuration")
            if not self.query_processing_configuration.classification_enabled:
                raise ValueError("adaptive pipelines require query classification")
        elif self.router_configuration_id is not None:
            raise ValueError("fixed pipelines cannot reference a router configuration")
        if self.reranker_configuration.enabled and self.retrieval_mode is RetrievalMode.NONE:
            raise ValueError("reranking cannot be enabled when retrieval mode is none")
        return self


class PipelineConfigurationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: int
    execution_mode: PipelineExecutionMode
    router_configuration_id: UUID | None
    adaptive_configuration: dict[str, Any]
    retrieval_mode: RetrievalMode
    lexical_configuration: dict[str, Any]
    dense_configuration: dict[str, Any]
    fusion_configuration: dict[str, Any]
    reranker_configuration: dict[str, Any]
    query_processing_configuration: dict[str, Any]
    context_configuration: dict[str, Any]
    generation_configuration: dict[str, Any]
    citation_configuration: dict[str, Any]
    prompt_versions: dict[str, Any]
    configuration_hash: str
    created_at: datetime
    frozen_at: datetime | None
