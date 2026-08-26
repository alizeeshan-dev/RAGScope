"""Versioned deterministic citation resolution/relevance/support verification."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from backend.app.evaluation.metrics.citation import (
    CitationClaim,
    CitationJudgment,
    CitationSupportLabel,
    CollectiveClaimJudgment,
)


@dataclass(frozen=True, slots=True)
class CitationVerifierConfiguration:
    version: str = "citation-lexical-verifier-v1"
    relevance_threshold: float = 0.2
    support_threshold: float = 0.75

    def __post_init__(self) -> None:
        if not 0.0 <= self.relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be between zero and one")
        if not 0.0 <= self.support_threshold <= 1.0:
            raise ValueError("support_threshold must be between zero and one")
        if self.relevance_threshold > self.support_threshold:
            raise ValueError("relevance_threshold cannot exceed support_threshold")


@dataclass(frozen=True, slots=True)
class CitationVerificationBatch:
    judgments: tuple[CitationJudgment, ...]
    collective_judgments: tuple[CollectiveClaimJudgment, ...]
    method: str
    version: str


class CitationVerifier(Protocol):
    @property
    def method(self) -> str: ...

    @property
    def version(self) -> str: ...

    def verify(
        self,
        claims: Sequence[CitationClaim],
        sources: Mapping[str, str],
    ) -> CitationVerificationBatch: ...


class DeterministicCitationVerifier:
    """Resolve IDs and estimate relevance/support using observable token overlap.

    This verifier intentionally does not claim full natural-language inference.
    It provides a free, deterministic baseline. Its ``method`` and ``version``
    make that limitation explicit so a model-assisted or NLI verifier can be
    plugged into the same protocol later.
    """

    method = "deterministic_token_coverage"

    def __init__(self, config: CitationVerifierConfiguration | None = None) -> None:
        self.config = config or CitationVerifierConfiguration()

    @property
    def version(self) -> str:
        return self.config.version

    def verify(
        self,
        claims: Sequence[CitationClaim],
        sources: Mapping[str, str],
    ) -> CitationVerificationBatch:
        judgments: list[CitationJudgment] = []
        collective: list[CollectiveClaimJudgment] = []
        for claim in claims:
            if not claim.factual:
                continue
            claim_tokens = _meaningful_tokens(claim.text)
            resolved_token_union: set[str] = set()
            for citation_id in claim.citation_ids:
                source_text = sources.get(citation_id)
                if source_text is None:
                    judgments.append(
                        CitationJudgment(
                            claim_id=claim.claim_id,
                            citation_id=citation_id,
                            exists=False,
                            automatic_relevant=False,
                            automatic_support="unsupported",
                            automatic_score=0.0,
                            automatic_method=self.method,
                            automatic_version=self.version,
                            details={"reason": "citation_id_does_not_resolve"},
                        )
                    )
                    continue
                source_tokens = _meaningful_tokens(source_text)
                resolved_token_union.update(source_tokens)
                coverage = _coverage(claim_tokens, source_tokens)
                relevant = coverage >= self.config.relevance_threshold
                support: CitationSupportLabel
                if coverage >= self.config.support_threshold:
                    support = "supported"
                elif relevant:
                    support = "partially_supported"
                else:
                    support = "unsupported"
                judgments.append(
                    CitationJudgment(
                        claim_id=claim.claim_id,
                        citation_id=citation_id,
                        exists=True,
                        automatic_relevant=relevant,
                        automatic_support=support,
                        automatic_score=coverage,
                        automatic_method=self.method,
                        automatic_version=self.version,
                        details={
                            "claim_token_count": len(claim_tokens),
                            "source_token_count": len(source_tokens),
                            "matched_claim_tokens": len(claim_tokens & source_tokens),
                        },
                    )
                )
            collective_score = _coverage(claim_tokens, resolved_token_union)
            collective.append(
                CollectiveClaimJudgment(
                    claim_id=claim.claim_id,
                    automatic_supported=(
                        bool(claim.citation_ids)
                        and collective_score >= self.config.support_threshold
                    ),
                    automatic_score=collective_score,
                    automatic_method=self.method,
                    automatic_version=self.version,
                )
            )
        return CitationVerificationBatch(
            judgments=tuple(judgments),
            collective_judgments=tuple(collective),
            method=self.method,
            version=self.version,
        )


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "with",
    }
)


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
        if token not in _STOPWORDS
    }


def _coverage(claim_tokens: set[str], source_tokens: set[str]) -> float:
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & source_tokens) / len(claim_tokens)
