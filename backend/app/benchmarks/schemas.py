from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QuestionType(StrEnum):
    DIRECT_FACT_LOOKUP = "direct_fact_lookup"
    DATASET_DISCOVERY = "dataset_discovery"
    DATASET_COMPARISON = "dataset_comparison"
    MULTI_DOCUMENT_SYNTHESIS = "multi_document_synthesis"
    MULTI_HOP_REASONING = "multi_hop_reasoning"
    TABLE_BASED = "table_based"
    BROAD_SUMMARY = "broad_summary"
    AMBIGUOUS = "ambiguous"
    UNANSWERABLE = "unanswerable"
    FALSE_PREMISE = "false_premise"
    CONTRADICTORY_SOURCE = "contradictory_source"
    DISTRACTOR_SENSITIVE = "distractor_sensitive"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class AnnotationStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"


class ExpectedAnswerability(StrEnum):
    ANSWERABLE = "answerable"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    UNANSWERABLE = "unanswerable"


class BenchmarkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must contain non-whitespace text")
        return value


class BenchmarkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class BenchmarkVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version_id: UUID
    version: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=10_000)


class BenchmarkVersionRead(BaseModel):
    id: UUID
    benchmark_id: UUID
    corpus_version_id: UUID
    version: int
    status: str
    notes: str | None
    created_at: datetime
    frozen_at: datetime | None
    question_count: int = 0


class BenchmarkQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str = Field(min_length=1, max_length=10_000)
    question_type: QuestionType
    difficulty: Difficulty
    answerable: bool
    expected_answerability: ExpectedAnswerability | None = None
    reference_answer: str | None = Field(default=None, max_length=50_000)
    answer_criteria: str | None = Field(default=None, max_length=50_000)
    unanswerable_explanation: str | None = Field(default=None, max_length=50_000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    annotation_notes: str | None = Field(default=None, max_length=50_000)
    annotation_status: AnnotationStatus = AnnotationStatus.DRAFT

    @field_validator("question_text")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question_text must contain non-whitespace text")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or len(normalized) > 100:
                raise ValueError("tags must be non-blank and at most 100 characters")
            if normalized not in seen:
                output.append(normalized)
                seen.add(normalized)
        return output

    @model_validator(mode="after")
    def validate_unanswerable_explanation(self) -> BenchmarkQuestionCreate:
        expected = self.expected_answerability or (
            ExpectedAnswerability.ANSWERABLE
            if self.answerable
            else ExpectedAnswerability.UNANSWERABLE
        )
        if self.answerable and expected is ExpectedAnswerability.UNANSWERABLE:
            raise ValueError("answerable questions cannot expect an unanswerable response")
        if not self.answerable and expected is not ExpectedAnswerability.UNANSWERABLE:
            raise ValueError("unanswerable questions must expect an unanswerable response")
        if not self.answerable and not _present(self.unanswerable_explanation):
            raise ValueError("unanswerable questions require an explanation")
        return self


class BenchmarkQuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str | None = Field(default=None, min_length=1, max_length=10_000)
    question_type: QuestionType | None = None
    difficulty: Difficulty | None = None
    answerable: bool | None = None
    expected_answerability: ExpectedAnswerability | None = None
    reference_answer: str | None = Field(default=None, max_length=50_000)
    answer_criteria: str | None = Field(default=None, max_length=50_000)
    unanswerable_explanation: str | None = Field(default=None, max_length=50_000)
    tags: list[str] | None = Field(default=None, max_length=100)
    annotation_notes: str | None = Field(default=None, max_length=50_000)
    annotation_status: AnnotationStatus | None = None

    @field_validator("question_text")
    @classmethod
    def question_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("question_text must contain non-whitespace text")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return BenchmarkQuestionCreate.normalize_tags(values)


class EvidenceReferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    page_number: int | None = Field(default=None, ge=1)
    element_id: UUID | None = None
    chunk_id: UUID | None = None
    selected_text: str = Field(min_length=1, max_length=100_000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=1)
    evidence_role: str = Field(
        default="required",
        pattern="^(required|alternative|contradiction|distractor)$",
    )

    @model_validator(mode="after")
    def require_source_object(self) -> EvidenceReferenceCreate:
        if self.element_id is None and self.chunk_id is None:
            raise ValueError("an evidence reference requires an element_id or chunk_id")
        if not self.selected_text.strip():
            raise ValueError("selected_text must contain non-whitespace text")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset and end_offset must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset must be greater than start_offset")
        return self


class EvidenceSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_number: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=10_000)
    references: list[EvidenceReferenceCreate] = Field(min_length=1, max_length=100)


class EvidenceReferenceRead(BaseModel):
    id: UUID
    evidence_set_id: UUID
    document_id: UUID
    page_number: int | None
    element_id: UUID | None
    chunk_id: UUID | None
    selected_text: str
    start_offset: int | None
    end_offset: int | None
    evidence_role: str
    created_at: datetime


class EvidenceSetRead(BaseModel):
    id: UUID
    benchmark_question_id: UUID
    set_number: int
    description: str | None
    created_at: datetime
    references: list[EvidenceReferenceRead]


class BenchmarkQuestionRead(BaseModel):
    id: UUID
    benchmark_version_id: UUID
    question_text: str
    question_type: str
    difficulty: str
    answerable: bool
    expected_answerability: str
    reference_answer: str | None
    answer_criteria: str | None
    unanswerable_explanation: str | None
    required_document_ids: list[UUID]
    required_chunk_ids: list[UUID]
    tags: list[str]
    annotation_notes: str | None
    annotation_status: str
    leakage_warning: bool
    leakage_score: float | None
    model_suggestion: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    acceptable_evidence_sets: list[EvidenceSetRead]


class LeakageCheckRead(BaseModel):
    warning: bool
    score: float


def _present(value: str | None) -> bool:
    return value is not None and bool(value.strip())
