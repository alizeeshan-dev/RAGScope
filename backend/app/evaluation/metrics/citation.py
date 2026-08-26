"""Citation metrics with explicit automatic and human judgment channels.

Definitions (all per query):

* existence rate: resolved emitted citation references / emitted references;
* precision: relevant emitted references / references with a relevance label;
* recall: mean best-set recall over human acceptable citation/evidence sets;
* claim support rate: factual claims supported by at least one citation / factual
  claims with support judgments (citation-free claims are observed unsupported);
* completeness: factual claims whose citations collectively support the whole
  claim / factual claims. It is missing when collective labels are incomplete.

An empty denominator is missing (``None``), never zero. A factual claim with no
citation, however, is an observed completeness/support failure and scores zero.
Infrastructure failures are excluded from every citation-quality denominator.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from backend.app.evaluation.contracts import EvaluationMethod, MetricOutput, MetricScope

CitationSupportLabel = Literal[
    "supported", "partially_supported", "unsupported", "contradicted", "not_evaluated"
]
JudgmentSource = Literal["automatic", "human"]

CITATION_METRIC_VERSION = "citation-metrics-v1"


@dataclass(frozen=True, slots=True)
class CitationClaim:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...] = ()
    factual: bool = True
    acceptable_citation_sets: tuple[frozenset[str], ...] | None = None

    def __post_init__(self) -> None:
        if not self.claim_id:
            raise ValueError("claim_id cannot be empty")
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("citation_ids must be unique within a claim")
        if self.acceptable_citation_sets is not None and any(
            not item for item in self.acceptable_citation_sets
        ):
            raise ValueError("acceptable citation sets cannot be empty")


@dataclass(frozen=True, slots=True)
class CitationJudgment:
    """One citation reference with preserved automatic and human decisions."""

    claim_id: str
    citation_id: str
    exists: bool
    automatic_relevant: bool | None = None
    automatic_support: CitationSupportLabel = "not_evaluated"
    automatic_score: float | None = None
    automatic_method: str = "not_evaluated"
    automatic_version: str = "not_evaluated"
    human_relevant: bool | None = None
    human_support: CitationSupportLabel | None = None
    human_score: float | None = None
    human_reviewer: str | None = None
    human_note: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("automatic_score", "human_score"):
            score = getattr(self, field_name)
            if score is not None and not 0.0 <= score <= 1.0:
                raise ValueError(f"{field_name} must be between zero and one")

    def relevance(self, source: JudgmentSource) -> bool | None:
        return self.human_relevant if source == "human" else self.automatic_relevant

    def support(self, source: JudgmentSource) -> CitationSupportLabel | None:
        return self.human_support if source == "human" else self.automatic_support

    def with_human_override(
        self,
        *,
        relevant: bool | None,
        support: CitationSupportLabel | None,
        score: float | None = None,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> CitationJudgment:
        """Return a reviewed copy without altering the automatic judgment."""

        return replace(
            self,
            human_relevant=relevant,
            human_support=support,
            human_score=score,
            human_reviewer=reviewer,
            human_note=note,
        )


@dataclass(frozen=True, slots=True)
class CollectiveClaimJudgment:
    claim_id: str
    automatic_supported: bool | None = None
    automatic_score: float | None = None
    automatic_method: str = "not_evaluated"
    automatic_version: str = "not_evaluated"
    human_supported: bool | None = None
    human_score: float | None = None
    human_reviewer: str | None = None
    human_note: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("automatic_score", "human_score"):
            score = getattr(self, field_name)
            if score is not None and not 0.0 <= score <= 1.0:
                raise ValueError(f"{field_name} must be between zero and one")

    def supported(self, source: JudgmentSource) -> bool | None:
        return self.human_supported if source == "human" else self.automatic_supported

    def with_human_override(
        self,
        *,
        supported: bool | None,
        score: float | None = None,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> CollectiveClaimJudgment:
        return replace(
            self,
            human_supported=supported,
            human_score=score,
            human_reviewer=reviewer,
            human_note=note,
        )


@dataclass(frozen=True, slots=True)
class CitationMetricInputs:
    claims: tuple[CitationClaim, ...]
    judgments: tuple[CitationJudgment, ...]
    collective_judgments: tuple[CollectiveClaimJudgment, ...] = ()
    infrastructure_failure_code: str | None = None


def evaluate_citation_metrics(
    inputs: CitationMetricInputs,
    *,
    judgment_source: JudgmentSource = "automatic",
) -> list[MetricOutput]:
    """Calculate citation metrics from stored claims and verifier/review labels."""

    method = (
        EvaluationMethod.HUMAN
        if judgment_source == "human"
        else EvaluationMethod.AUTOMATED
    )
    base_details: dict[str, object] = {
        "judgment_source": judgment_source,
        "excluded_infrastructure_failure": inputs.infrastructure_failure_code is not None,
    }
    if inputs.infrastructure_failure_code is not None:
        return [
            MetricOutput(
                name=name,
                version=CITATION_METRIC_VERSION,
                scope=MetricScope.CITATION,
                value=None,
                method=method,
                details={**base_details, "failure_code": inputs.infrastructure_failure_code},
            )
            for name in (
                "citation_existence_rate",
                "citation_precision",
                "citation_recall",
                "claim_support_rate",
                "citation_completeness",
            )
        ]

    factual_claims = [claim for claim in inputs.claims if claim.factual]
    judgment_by_key = {
        (judgment.claim_id, judgment.citation_id): judgment for judgment in inputs.judgments
    }
    emitted = [
        (claim, citation_id)
        for claim in factual_claims
        for citation_id in claim.citation_ids
    ]
    emitted_judgments = [
        judgment_by_key.get((claim.claim_id, citation_id)) for claim, citation_id in emitted
    ]

    existence_denominator = len(emitted)
    existence_numerator = sum(
        judgment is not None and judgment.exists for judgment in emitted_judgments
    )
    existence = (
        existence_numerator / existence_denominator if existence_denominator else None
    )

    relevance_labels = [
        judgment.relevance(judgment_source)
        for judgment in emitted_judgments
        if judgment is not None and judgment.relevance(judgment_source) is not None
    ]
    precision = (
        sum(label is True for label in relevance_labels) / len(relevance_labels)
        if relevance_labels
        else None
    )

    recall_scores: list[float] = []
    selected_sets: dict[str, list[str]] = {}
    for claim in factual_claims:
        if claim.acceptable_citation_sets is None:
            continue
        existing_ids = {
            citation_id
            for citation_id in claim.citation_ids
            if (
                (judgment := judgment_by_key.get((claim.claim_id, citation_id))) is not None
                and judgment.exists
            )
        }
        scored_sets = [
            (len(existing_ids & expected) / len(expected), expected)
            for expected in claim.acceptable_citation_sets
        ]
        best_score, best_set = max(scored_sets, key=lambda item: item[0])
        recall_scores.append(best_score)
        selected_sets[claim.claim_id] = sorted(best_set)
    recall = sum(recall_scores) / len(recall_scores) if recall_scores else None

    supported_claim_values: list[bool] = []
    support_missing_claims: list[str] = []
    for claim in factual_claims:
        if not claim.citation_ids:
            supported_claim_values.append(False)
            continue
        support_labels = [
            judgment.support(judgment_source)
            for citation_id in claim.citation_ids
            if (judgment := judgment_by_key.get((claim.claim_id, citation_id))) is not None
            and judgment.support(judgment_source) is not None
            and judgment.support(judgment_source) != "not_evaluated"
        ]
        if not support_labels:
            support_missing_claims.append(claim.claim_id)
            continue
        supported_claim_values.append("supported" in support_labels)
    support_rate = (
        sum(supported_claim_values) / len(supported_claim_values)
        if supported_claim_values
        else None
    )

    collective_by_claim = {
        judgment.claim_id: judgment for judgment in inputs.collective_judgments
    }
    collective_values: list[bool] = []
    collective_missing_claims: list[str] = []
    for claim in factual_claims:
        if not claim.citation_ids:
            collective_values.append(False)
            continue
        collective = collective_by_claim.get(claim.claim_id)
        value = collective.supported(judgment_source) if collective is not None else None
        if value is None:
            collective_missing_claims.append(claim.claim_id)
        else:
            collective_values.append(value)
    # Completeness needs a judgment for every cited factual claim. A partial
    # human review is missing rather than being silently averaged upward.
    completeness = (
        None
        if collective_missing_claims
        else (
            sum(collective_values) / len(collective_values)
            if collective_values
            else None
        )
    )

    return [
        MetricOutput(
            name="citation_existence_rate",
            version=CITATION_METRIC_VERSION,
            scope=MetricScope.CITATION,
            value=existence,
            method=EvaluationMethod.DETERMINISTIC,
            details={
                **base_details,
                "numerator": existence_numerator,
                "denominator": existence_denominator,
                "definition": "resolved emitted references / emitted references",
            },
        ),
        MetricOutput(
            name="citation_precision",
            version=CITATION_METRIC_VERSION,
            scope=MetricScope.CITATION,
            value=precision,
            method=method,
            details={
                **base_details,
                "relevant": sum(label is True for label in relevance_labels),
                "judged_references": len(relevance_labels),
            },
        ),
        MetricOutput(
            name="citation_recall",
            version=CITATION_METRIC_VERSION,
            scope=MetricScope.CITATION,
            value=recall,
            method=EvaluationMethod.DETERMINISTIC,
            details={
                **base_details,
                "definition": "mean per-claim best acceptable-set recall",
                "evaluable_claims": len(recall_scores),
                "selected_acceptable_sets": selected_sets,
            },
        ),
        MetricOutput(
            name="claim_support_rate",
            version=CITATION_METRIC_VERSION,
            scope=MetricScope.CITATION,
            value=support_rate,
            method=method,
            details={
                **base_details,
                "supported_claims": sum(supported_claim_values),
                "judged_or_citation_free_claims": len(supported_claim_values),
                "missing_claim_ids": support_missing_claims,
            },
        ),
        MetricOutput(
            name="citation_completeness",
            version=CITATION_METRIC_VERSION,
            scope=MetricScope.CITATION,
            value=completeness,
            method=method,
            details={
                **base_details,
                "collectively_supported_claims": sum(collective_values),
                "factual_claims": len(factual_claims),
                "missing_collective_claim_ids": collective_missing_claims,
            },
        ),
    ]
