from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.db.models import Answerability


class GroundedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)
    citations: list[str] = Field(default_factory=list, max_length=100)
    claim_type: Literal["factual", "non_factual"] = "factual"

    @field_validator("citations")
    @classmethod
    def validate_citation_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("claim citations must be unique")
        for citation_id in value:
            if (
                len(citation_id) > 30
                or not citation_id.startswith("S")
                or not citation_id[1:].isdigit()
                or citation_id[1:].startswith("0")
            ):
                raise ValueError(f"invalid citation identifier: {citation_id}")
        return value

    @model_validator(mode="after")
    def require_factual_citation(self) -> Self:
        if self.claim_type == "factual" and not self.citations:
            raise ValueError("every factual claim requires at least one citation")
        return self


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerability: Answerability
    answer: str = Field(min_length=1, max_length=100_000)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=1_000)
    limitations: list[str] = Field(default_factory=list, max_length=1_000)
    abstention_reason: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def validate_answerability_shape(self) -> Self:
        if self.answerability == Answerability.ANSWERABLE:
            if self.abstention_reason is not None:
                raise ValueError("answerable output cannot include an abstention reason")
            if not self.claims:
                raise ValueError("answerable output requires explicit claims")
        elif self.answerability == Answerability.PARTIALLY_ANSWERABLE:
            if not self.claims:
                raise ValueError("partially answerable output requires explicit claims")
            if not self.limitations:
                raise ValueError("partially answerable output requires limitations")
        elif self.answerability == Answerability.UNANSWERABLE:
            if not self.abstention_reason:
                raise ValueError("unanswerable output requires an abstention reason")
            if any(claim.claim_type == "factual" for claim in self.claims):
                raise ValueError("unanswerable output cannot contain factual claims")
        return self
