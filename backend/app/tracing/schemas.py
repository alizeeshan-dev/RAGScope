from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TRACE_SCHEMA_VERSION: Literal["ragscope.observable-trace.v1"] = (
    "ragscope.observable-trace.v1"
)


class TraceSpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query_run_id: UUID
    parent_span_id: UUID | None
    sequence_number: int
    span_type: str
    name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    latency_ms: int | None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    configuration_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None
    artifact_ids: list[str] = Field(default_factory=list)


class ArtifactReference(BaseModel):
    id: UUID
    query_run_id: UUID | None
    trace_span_id: UUID | None
    artifact_type: str
    content_hash: str
    media_type: str
    producing_operation: str
    producer_version: str | None
    configuration: dict[str, Any] = Field(default_factory=dict)
    size_bytes: int


class RankMovement(BaseModel):
    chunk_id: UUID
    retrieval_rank: int | None
    fused_rank: int | None
    reranked_rank: int | None
    movement: int | None
    selected_for_context: bool


class TraceSummary(BaseModel):
    total_latency_ms: int | None
    latency_by_stage_ms: dict[str, int]
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    retrieval_candidate_count: int
    reranked_candidate_count: int
    selected_context_count: int
    excluded_context_count: int
    retrieved_document_ids: list[UUID]
    rank_movement: list[RankMovement]
    evidence_flow: dict[str, int]
    unsupported_claim_count: int
    not_evaluated_claim_count: int
    failure_stage: str | None


class TraceRunSnapshot(BaseModel):
    id: UUID
    corpus_version_id: UUID
    corpus_version: dict[str, Any]
    pipeline_configuration_id: UUID
    prompt_template_id: UUID | None
    status: str
    original_query: str
    normalized_query: str
    rewritten_query: str | None
    classification: dict[str, Any]
    configured_route: dict[str, Any]
    answerability: str | None
    failure_code: str | None


class ObservableTraceExport(BaseModel):
    schema_version: Literal["ragscope.observable-trace.v1"] = TRACE_SCHEMA_VERSION
    exported_at: datetime
    run: TraceRunSnapshot
    pipeline_configuration: dict[str, Any]
    prompt: dict[str, Any] | None
    spans: list[TraceSpanRead]
    artifacts: list[ArtifactReference]
    summary: TraceSummary
