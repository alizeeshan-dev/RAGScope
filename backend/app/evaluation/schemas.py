from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.models import EvaluationMetricScope


class EvaluationResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query_run_id: UUID
    metric_name: str
    metric_scope: EvaluationMetricScope
    metric_value: float | None
    metric_version: str
    evaluation_method: str
    details: dict[str, Any]
    input_snapshot: dict[str, Any]
    input_hash: str
    created_at: datetime


class FailureAttributionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query_run_id: UUID
    sequence_number: int
    is_primary: bool
    pipeline_stage: str
    automatic_label: str
    attribution_rule: str
    evidence: dict[str, Any]
    input_hash: str
    taxonomy_version: str
    rules_version: str
    human_override_label: str | None
    human_override_note: str | None
    human_reviewed_at: datetime | None
    created_at: datetime


class EvaluationBundleRead(BaseModel):
    query_run_id: UUID
    benchmark_question_id: UUID | None
    metrics: list[EvaluationResultRead]
    failure_attributions: list[FailureAttributionRead]


class EvaluationTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_versions: list[str] | None = Field(default=None, max_length=100)


class HumanMetricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_name: Literal[
        "answer_correctness",
        "answer_completeness",
        "partial_answer_accuracy",
        "false_premise_recognition",
        "unsupported_claim_count.human",
        "contradiction_count.human",
    ]
    metric_value: float | None = Field(default=None, ge=0)
    reviewer_label: str = Field(min_length=1, max_length=255)
    reviewer_note: str | None = Field(default=None, max_length=20_000)


class FailureOverrideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    reviewer_note: str = Field(min_length=1, max_length=20_000)


class CitationVerificationOverrideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal[
        "supported",
        "partially_supported",
        "unsupported",
        "contradicted",
        "irrelevant",
    ]
    score: float | None = Field(default=None, ge=0, le=1)
    reviewer_note: str = Field(min_length=1, max_length=20_000)


class CitationVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    citation_id: UUID
    method: str
    verifier_version: str
    model_id: str | None
    automatic_label: str
    automatic_score: float | None
    details: dict[str, Any]
    human_label: str | None
    human_score: float | None
    human_note: str | None
    human_reviewed_at: datetime | None
    created_at: datetime

