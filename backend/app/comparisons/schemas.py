from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.query_runtime.schemas import QueryFilters


class QueryComparisonCreate(BaseModel):
    """One question and corpus evaluated by two to four frozen configurations."""

    model_config = ConfigDict(extra="forbid")

    corpus_version_id: UUID
    question: str = Field(min_length=1, max_length=10_000)
    pipeline_configuration_ids: list[UUID] = Field(min_length=2, max_length=4)
    filters: QueryFilters = Field(default_factory=QueryFilters)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain non-whitespace text")
        # Preserve the exact user input. QueryRun normalization remains observable.
        return value

    @field_validator("pipeline_configuration_ids")
    @classmethod
    def require_distinct_pipelines(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("pipeline configurations must be distinct")
        return values


class ConfigurationFacet(BaseModel):
    key: str
    label: str
    values: list[Any]


class ComparisonPipeline(BaseModel):
    id: UUID
    name: str
    version: int
    configuration_hash: str
    retrieval_mode: str
    facets: dict[str, Any]
    frozen_snapshot: dict[str, Any]


class ComparisonStage(BaseModel):
    sequence_number: int
    name: str
    span_type: str
    status: str
    latency_ms: int | None
    error_code: str | None


class ComparisonContextSource(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str | None
    citation_id: str | None
    sequence_number: int
    selected: bool
    exclusion_reason: str | None
    token_count: int
    page_start: int | None
    page_end: int | None
    text: str


class ComparisonCitation(BaseModel):
    citation_id: str
    chunk_id: UUID
    document_id: UUID
    page_number: int | None
    referenced_text: str


class ComparisonClaim(BaseModel):
    sequence_number: int
    text: str
    support_status: str
    citations: list[ComparisonCitation]


class ComparisonColumn(BaseModel):
    position: int
    query_run_id: UUID
    pipeline: ComparisonPipeline
    run_status: str
    original_query: str
    rewritten_query: str | None
    configured_route: dict[str, Any]
    classification: dict[str, Any]
    answerability: str | None
    answer: str | None
    limitations: list[str]
    abstention_reason: str | None
    context_artifact_id: UUID | None
    context_sources: list[ComparisonContextSource]
    claims: list[ComparisonClaim]
    total_latency_ms: int | None
    stage_latency_ms: dict[str, int]
    timeline: list[ComparisonStage]
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    cost_currency: str | None
    failure_code: str | None
    failure_message: str | None


class EvidenceCell(BaseModel):
    query_run_id: UUID
    pipeline_configuration_id: UUID
    present: bool
    lexical_rank: int | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    fused_rank: int | None = None
    fusion_score: float | None = None
    reranked_rank: int | None = None
    reranker_score: float | None = None
    selected_for_context: bool = False
    exclusion_reason: str | None = None
    citation_id: str | None = None


class EvidenceRow(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str | None
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    text: str
    cells: list[EvidenceCell]


class EvidenceOverlap(BaseModel):
    left_query_run_id: UUID
    right_query_run_id: UUID
    shared_chunk_ids: list[UUID]
    left_only_chunk_ids: list[UUID]
    right_only_chunk_ids: list[UUID]
    jaccard: float | None


class QueryComparisonRead(BaseModel):
    id: UUID
    corpus_version_id: UUID
    original_question: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    failure_message: str | None
    configuration_differences: list[ConfigurationFacet]
    evidence_overlap: list[EvidenceOverlap]
    evidence_rows: list[EvidenceRow]
    columns: list[ComparisonColumn]
