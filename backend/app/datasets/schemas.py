from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATASET_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "domain",
    "modalities",
    "task_types",
    "instance_count",
    "participant_count",
    "annotation_types",
    "languages",
    "license",
    "access_url",
    "human_ratings",
    "collection_method",
    "known_limitations",
)

LIST_FIELDS = frozenset({"modalities", "task_types", "annotation_types", "languages"})
INTEGER_FIELDS = frozenset({"instance_count", "participant_count"})


class ExtractionStrategy(StrEnum):
    BASELINE = "baseline"
    RETRIEVAL_ASSISTED = "retrieval_assisted"


class ValueState(StrEnum):
    STATED = "stated"
    NOT_STATED = "not_stated"


class ExtractedField(BaseModel):
    """One syntactically valid model field, distinct from human approval."""

    model_config = ConfigDict(extra="forbid")

    state: ValueState
    value: str | int | list[str] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence IDs must be unique")
        if any(not item.startswith("E") or not item[1:].isdigit() for item in value):
            raise ValueError("evidence IDs must use the application-issued E<number> format")
        return value

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.state is ValueState.NOT_STATED:
            if self.value is not None or self.evidence_ids:
                raise ValueError("not_stated fields cannot contain a value or evidence IDs")
        elif self.value is None:
            raise ValueError("stated fields require a non-null value")
        elif not self.evidence_ids:
            raise ValueError("every stated field requires evidence")
        return self


class DatasetExtractionOutput(BaseModel):
    """Versioned, strict structured output accepted from an extraction provider."""

    model_config = ConfigDict(extra="forbid")

    name: ExtractedField
    description: ExtractedField
    domain: ExtractedField
    modalities: ExtractedField
    task_types: ExtractedField
    instance_count: ExtractedField
    participant_count: ExtractedField
    annotation_types: ExtractedField
    languages: ExtractedField
    license: ExtractedField
    access_url: ExtractedField
    human_ratings: ExtractedField
    collection_method: ExtractedField
    known_limitations: ExtractedField

    @model_validator(mode="after")
    def validate_field_types(self) -> Self:
        for field_name in DATASET_FIELDS:
            item = getattr(self, field_name)
            if item.state is ValueState.NOT_STATED:
                continue
            value = item.value
            if field_name in LIST_FIELDS:
                if (
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(entry, str) or not entry.strip() for entry in value)
                ):
                    raise ValueError(f"{field_name} must be a non-empty list of strings")
            elif field_name in INTEGER_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{field_name} must be a non-negative integer")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            if isinstance(value, str) and len(value) > 50_000:
                raise ValueError(f"{field_name} exceeds the maximum length")
            if isinstance(value, list) and (
                len(value) > 100 or any(len(item) > 1_000 for item in value)
            ):
                raise ValueError(f"{field_name} exceeds the list limits")
            if field_name == "access_url" and isinstance(value, str):
                parsed = urlsplit(value)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                    or len(value) > 2_048
                ):
                    raise ValueError("access_url must be an absolute HTTP(S) URL")
        return self


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    document_id: UUID
    chunk_id: UUID
    element_id: UUID | None = None
    page_number: int | None = None
    supporting_text: str


class DatasetExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: ExtractionStrategy = ExtractionStrategy.BASELINE
    provider: Literal["fake", "gemini", "openai_compatible"] = "fake"
    model: str | None = Field(default=None, max_length=255)
    prompt_version: Literal["dataset-extraction-v1"] = "dataset-extraction-v1"
    field_schema_version: Literal["dataset-record.v1"] = "dataset-record.v1"
    retrieval_candidate_count: int = Field(default=12, ge=1, le=100)
    maximum_source_chunks: int = Field(default=100, ge=1, le=500)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4096, ge=128, le=32768)


class DatasetExtractionRunRead(BaseModel):
    job_id: UUID
    record_ids: list[UUID]
    status: str


class FieldEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_record_id: UUID
    field_name: str
    document_id: UUID
    page_number: int | None
    element_id: UUID | None
    chunk_id: UUID | None
    supporting_text: str
    extraction_method: str
    model_confidence_label: str | None
    review_status: str
    reviewer_note: str | None


class DatasetFieldRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_record_id: UUID
    field_name: str
    action: str
    previous_value: Any = None
    new_value: Any = None
    previous_state: str | None
    new_state: str
    reviewer_note: str | None
    evidence_backed: bool
    created_at: datetime


class DatasetRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_version_id: UUID
    source_document_id: UUID
    name: str | None
    description: str | None
    domain: str | None
    modalities: list[str] | None
    task_types: list[str] | None
    instance_count: int | None
    participant_count: int | None
    annotation_types: list[str] | None
    languages: list[str] | None
    license: str | None
    access_url: str | None
    human_ratings: str | None
    collection_method: str | None
    known_limitations: str | None
    extraction_status: str
    review_status: str
    strategy: str
    original_values: dict[str, Any]
    current_values: dict[str, Any]
    field_states: dict[str, Any]
    extraction_configuration: dict[str, Any]
    raw_response_artifact_id: UUID | None
    structured_result_artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime
    evidence: list[FieldEvidenceRead] = Field(default_factory=list)
    review_history: list[DatasetFieldRevisionRead] = Field(default_factory=list)


class DatasetReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "accept",
        "edit",
        "reject",
        "clear",
        "mark_not_stated",
        "approve_record",
        "reopen_record",
    ]
    field_name: str | None = None
    value: str | int | list[str] | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    reviewer_note: str | None = Field(default=None, max_length=10_000)
    reviewer_label: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        record_action = self.action in {"approve_record", "reopen_record"}
        if record_action and self.field_name is not None:
            raise ValueError("record actions do not accept field_name")
        if not record_action and self.field_name not in DATASET_FIELDS:
            raise ValueError("field_name must be a supported dataset field")
        if self.action == "edit" and self.value is None:
            raise ValueError("edit requires a non-null value")
        if self.action != "edit" and self.value is not None:
            raise ValueError("only edit accepts a value")
        return self


class HumanFieldEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    document_id: UUID
    page_number: int | None = Field(default=None, ge=1)
    element_id: UUID | None = None
    chunk_id: UUID | None = None
    selected_text: str = Field(min_length=1, max_length=100_000)
    reviewer_note: str | None = Field(default=None, max_length=10_000)

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, value: str) -> str:
        if value not in DATASET_FIELDS:
            raise ValueError("field_name must be a supported dataset field")
        return value

    @model_validator(mode="after")
    def require_source(self) -> Self:
        if self.element_id is None and self.chunk_id is None:
            raise ValueError("element_id or chunk_id is required")
        return self


class DatasetRecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: dict[str, str | int | list[str] | None] = Field(min_length=1, max_length=14)
    evidence_ids: dict[str, list[UUID]] = Field(default_factory=dict)
    reviewer_note: str | None = Field(default=None, max_length=10_000)

    @field_validator("updates")
    @classmethod
    def supported_fields(
        cls, value: dict[str, str | int | list[str] | None]
    ) -> dict[str, str | int | list[str] | None]:
        unsupported = set(value) - set(DATASET_FIELDS)
        if unsupported:
            raise ValueError(f"unsupported dataset fields: {sorted(unsupported)}")
        return value


class DatasetComparisonRequest(BaseModel):
    record_ids: list[UUID] = Field(min_length=2, max_length=4)

    @field_validator("record_ids")
    @classmethod
    def unique_records(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("comparison record IDs must be unique")
        return value


class DatasetComparisonRead(BaseModel):
    corpus_version_id: UUID
    fields: dict[str, dict[str, Any]]
    records: list[DatasetRecordRead]
