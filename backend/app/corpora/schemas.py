from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.models import CorpusVersionStatus


class CorpusCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    domain: str | None = Field(default=None, max_length=255)


class CorpusVersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=100)
    parser_configuration: dict[str, Any] = Field(default_factory=dict)
    chunker_configuration: dict[str, Any] = Field(default_factory=dict)
    embedding_configuration: dict[str, Any] = Field(default_factory=dict)


class CorpusVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_id: UUID
    version_label: str
    status: CorpusVersionStatus
    document_count: int
    content_hash: str | None
    parser_configuration: dict[str, Any]
    chunker_configuration: dict[str, Any]
    embedding_configuration: dict[str, Any]
    created_at: datetime
    frozen_at: datetime | None


class CorpusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    domain: str | None
    created_at: datetime
    updated_at: datetime


class CorpusDetail(CorpusRead):
    versions: list[CorpusVersionRead]
