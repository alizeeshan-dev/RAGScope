from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.db.models import Answerability, QueryRunStatus


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(default_factory=list, max_length=500)
    publication_years: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("publication_years")
    @classmethod
    def validate_years(cls, values: list[int]) -> list[int]:
        if any(year < 1000 or year > 9999 for year in values):
            raise ValueError("publication years must be four-digit years")
        return list(dict.fromkeys(values))


class QueryRunCreate(BaseModel):
    corpus_version_id: UUID
    pipeline_configuration_id: UUID
    benchmark_question_id: UUID | None = None
    query_text: str
    filters: QueryFilters = Field(default_factory=QueryFilters)


class QueryRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_version_id: UUID
    benchmark_question_id: UUID | None
    pipeline_configuration_id: UUID
    prompt_template_id: UUID | None
    query_text: str
    normalized_query: str
    rewritten_query: str | None
    status: QueryRunStatus
    route_decision: dict[str, Any]
    classification: dict[str, Any]
    extracted_metadata: dict[str, Any]
    answer_text: str | None
    answerability_decision: Answerability | None
    limitations: list[str]
    abstention_reason: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    failure_code: str | None
    failure_message: str | None
    generation_metadata: dict[str, Any]
    context_artifact_id: UUID | None
    raw_response_artifact_id: UUID | None
    experiment_run_id: UUID | None
    experiment_attempt_number: int | None


class RetrievalResultRead(BaseModel):
    id: UUID
    chunk_id: UUID
    document_id: UUID
    document_title: str | None = None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section_path: list[str] = Field(default_factory=list)
    retriever_type: str
    original_rank: int | None
    original_score: float | None
    normalized_score: float | None
    fused_rank: int | None
    fusion_score: float | None
    reranked_rank: int | None
    reranker_score: float | None
    selected_for_context: bool
    timing_ms: int | None
    metadata: dict[str, Any]


class CitationRead(BaseModel):
    id: UUID
    citation_id: str
    chunk_id: UUID
    document_id: UUID
    document_title: str | None = None
    page_number: int | None
    referenced_text: str
    entailment_status: str
    entailment_score: float | None


class ClaimRead(BaseModel):
    id: UUID
    sequence_number: int
    claim_text: str
    claim_type: str
    citation_ids: list[str]
    support_status: str
    verification_method: str
    verification_score: float | None
    citations: list[CitationRead]


class ContextSourceRead(BaseModel):
    id: UUID
    chunk_id: UUID
    document_id: UUID
    document_title: str | None = None
    citation_id: str | None
    sequence_number: int
    selected: bool
    exclusion_reason: str | None
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: list[str] = Field(default_factory=list)
    text: str
