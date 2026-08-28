from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.models import IndexStatus, IndexType, JobStatus


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    status: JobStatus
    progress_current: int
    progress_total: int
    retry_count: int
    error_code: str | None
    error_message: str | None
    result_artifact_ids: list[str]
    started_at: datetime | None
    finished_at: datetime | None
    available_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    cancellation_requested_at: datetime | None
    max_attempts: int


class OperationAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    job_type: str
    status: JobStatus
    resource_type: str
    resource_id: UUID
    status_url: str


class IndexStatusRead(BaseModel):
    id: UUID
    index_type: IndexType
    status: IndexStatus
    configuration: dict[str, Any]
    provider_id: str | None
    model_id: str | None
    embedding_dimension: int | None
    preprocessing_version: str | None
    similarity_method: str | None
    chunk_count: int
    indexed_count: int
    failure_count: int
    integrity_valid: bool
    integrity_errors: list[str]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=10, ge=1, le=100)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    metadata_equals: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SearchResultRead(BaseModel):
    chunk_id: UUID
    document_id: UUID
    corpus_version_id: UUID
    text: str
    rank: int
    score: float
    retrieval_method: str
    metadata: dict[str, Any]
