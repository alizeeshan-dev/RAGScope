from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from .taxonomy import (
    ATTRIBUTION_RULES_VERSION,
    FAILURE_TAXONOMY_VERSION,
    FailureCategory,
)


@dataclass(frozen=True, slots=True)
class AttributionInputs:
    acceptable_chunk_sets: tuple[frozenset[UUID], ...] | None = None
    required_document_ids: frozenset[UUID] = frozenset()
    retrieved_chunk_ids: frozenset[UUID] = frozenset()
    retrieved_document_ids: frozenset[UUID] = frozenset()
    reranked_chunk_ids: frozenset[UUID] = frozenset()
    reranking_applied: bool = False
    context_chunk_ids: frozenset[UUID] = frozenset()
    answer_correctness: float | None = None
    answer_completeness: float | None = None
    expected_answerable: bool | None = None
    actual_answerability: str | None = None
    unsupported_claim_count: int | None = None
    contradicted_claim_count: int | None = None
    missing_citation_count: int = 0
    invalid_citation_count: int = 0
    irrelevant_citation_count: int = 0
    partial_support_count: int = 0
    redundancy_rate: float | None = None
    unmanaged_conflict: bool = False
    explicit_signals: frozenset[FailureCategory] = frozenset()
    infrastructure_failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class Attribution:
    label: FailureCategory
    stage: str
    rule: str
    evidence: dict[str, object] = field(default_factory=dict)
    is_primary: bool = False
    taxonomy_version: str = FAILURE_TAXONOMY_VERSION
    rules_version: str = ATTRIBUTION_RULES_VERSION


def attribute_failures(inputs: AttributionInputs) -> list[Attribution]:
    """Apply earliest-observable-stage rules without inferring hidden reasoning."""

    candidates: list[Attribution] = []
    infrastructure = _infrastructure(inputs.infrastructure_failure_code)
    if infrastructure is not None:
        return [_primary(infrastructure)]

    candidates.extend(_explicit(inputs.explicit_signals))
    if inputs.acceptable_chunk_sets is not None and inputs.acceptable_chunk_sets:
        retrieved = _best_coverage(inputs.retrieved_chunk_ids, inputs.acceptable_chunk_sets)
        reranked_ids = (
            inputs.reranked_chunk_ids
            if inputs.reranking_applied
            else inputs.retrieved_chunk_ids
        )
        reranked = _best_coverage(reranked_ids, inputs.acceptable_chunk_sets)
        context = _best_coverage(inputs.context_chunk_ids, inputs.acceptable_chunk_sets)
        evidence: dict[str, object] = {
            "retrieval_completeness": retrieved,
            "reranking_completeness": reranked,
            "context_completeness": context,
        }
        if retrieved == 0:
            candidates.append(
                Attribution(
                    FailureCategory.RETRIEVAL_TOTAL_MISS,
                    "retrieval",
                    "required_evidence_absent_from_retrieved_top_k",
                    evidence,
                )
            )
        elif retrieved < 1:
            candidates.append(
                Attribution(
                    FailureCategory.RETRIEVAL_PARTIAL_EVIDENCE,
                    "retrieval",
                    "retrieved_top_k_contains_only_part_of_best_evidence_set",
                    evidence,
                )
            )
        elif reranked < 1:
            candidates.append(
                Attribution(
                    FailureCategory.RERANK_REQUIRED_EVIDENCE_DEMOTED,
                    "reranking",
                    "complete_retrieved_evidence_not_present_after_reranking",
                    evidence,
                )
            )
        elif context < 1:
            candidates.append(
                Attribution(
                    FailureCategory.CONTEXT_REQUIRED_EVIDENCE_DROPPED,
                    "context",
                    "complete_reranked_evidence_not_present_in_final_context",
                    evidence,
                )
            )
        elif inputs.answer_correctness is not None and inputs.answer_correctness < 1:
            candidates.append(
                Attribution(
                    FailureCategory.GENERATION_IGNORED_EVIDENCE,
                    "generation",
                    "complete_required_evidence_present_but_answer_not_correct",
                    {**evidence, "answer_correctness": inputs.answer_correctness},
                )
            )

    if inputs.required_document_ids and not (
        inputs.retrieved_document_ids & inputs.required_document_ids
    ):
        candidates.append(
            Attribution(
                FailureCategory.RETRIEVAL_WRONG_DOCUMENT,
                "retrieval",
                "no_required_document_in_retrieved_results",
                {
                    "required_document_ids": sorted(
                        str(value) for value in inputs.required_document_ids
                    ),
                    "retrieved_document_ids": sorted(
                        str(value) for value in inputs.retrieved_document_ids
                    ),
                },
            )
        )
    if inputs.retrieved_chunk_ids and inputs.acceptable_chunk_sets:
        relevant = set().union(*inputs.acceptable_chunk_sets)
        distractor_rate = 1 - len(inputs.retrieved_chunk_ids & relevant) / len(
            inputs.retrieved_chunk_ids
        )
        if distractor_rate > 0.5:
            candidates.append(
                Attribution(
                    FailureCategory.RETRIEVAL_DISTRACTOR_DOMINANCE,
                    "retrieval",
                    "more_than_half_of_retrieved_chunks_are_not_annotated_evidence",
                    {"distractor_rate": distractor_rate},
                )
            )
    if inputs.redundancy_rate is not None and inputs.redundancy_rate > 0.5:
        candidates.append(
            Attribution(
                FailureCategory.CONTEXT_EXCESSIVE_REDUNDANCY,
                "context",
                "context_redundancy_rate_exceeds_0_5",
                {"redundancy_rate": inputs.redundancy_rate},
            )
        )
    if inputs.unmanaged_conflict:
        candidates.append(
            Attribution(
                FailureCategory.CONTEXT_CONFLICT_UNMANAGED,
                "context",
                "human_annotation_marks_conflicting_evidence_as_unmanaged",
            )
        )
    candidates.extend(_generation_and_citation(inputs))
    return _deduplicate_and_mark_primary(candidates)


def _generation_and_citation(inputs: AttributionInputs) -> list[Attribution]:
    output: list[Attribution] = []
    if inputs.answer_completeness is not None and inputs.answer_completeness < 1:
        output.append(
            Attribution(
                FailureCategory.GENERATION_INCOMPLETE_ANSWER,
                "generation",
                "human_or_versioned_judge_marks_answer_incomplete",
                {"answer_completeness": inputs.answer_completeness},
            )
        )
    if inputs.unsupported_claim_count:
        output.append(
            Attribution(
                FailureCategory.GENERATION_UNSUPPORTED_CLAIM,
                "generation",
                "one_or_more_claims_are_unsupported",
                {"count": inputs.unsupported_claim_count},
            )
        )
    if inputs.contradicted_claim_count:
        output.append(
            Attribution(
                FailureCategory.GENERATION_CONTRADICTED_EVIDENCE,
                "generation",
                "one_or_more_claims_are_contradicted",
                {"count": inputs.contradicted_claim_count},
            )
        )
    if inputs.expected_answerable is False and inputs.actual_answerability != "unanswerable":
        output.append(
            Attribution(
                FailureCategory.GENERATION_FAILED_TO_ABSTAIN,
                "generation",
                "benchmark_is_unanswerable_but_run_did_not_abstain",
            )
        )
    if inputs.expected_answerable is True and inputs.actual_answerability == "unanswerable":
        output.append(
            Attribution(
                FailureCategory.GENERATION_INCORRECT_ABSTENTION,
                "generation",
                "benchmark_is_answerable_but_run_abstained",
            )
        )
    for count, label, rule in (
        (inputs.missing_citation_count, FailureCategory.CITATION_MISSING, "claim_missing_citation"),
        (
            inputs.invalid_citation_count,
            FailureCategory.CITATION_INVALID_ID,
            "citation_id_unresolved",
        ),
        (
            inputs.irrelevant_citation_count,
            FailureCategory.CITATION_IRRELEVANT,
            "citation_verifier_marks_source_irrelevant",
        ),
        (
            inputs.partial_support_count,
            FailureCategory.CITATION_PARTIAL_SUPPORT,
            "citations_do_not_collectively_support_complete_claim",
        ),
    ):
        if count:
            output.append(Attribution(label, "citation", rule, {"count": count}))
    return output


def _best_coverage(found: frozenset[UUID], sets: tuple[frozenset[UUID], ...]) -> float:
    return max((len(found & item) / len(item) if item else 1.0 for item in sets), default=0.0)


def _infrastructure(code: str | None) -> Attribution | None:
    aliases = {
        "GENERATION_FAILED": FailureCategory.MODEL_PROVIDER_FAILURE,
        "MODEL_PROVIDER_FAILURE": FailureCategory.MODEL_PROVIDER_FAILURE,
        "EMBEDDING_PROVIDER_FAILURE": FailureCategory.EMBEDDING_PROVIDER_FAILURE,
        "DATABASE_FAILURE": FailureCategory.DATABASE_FAILURE,
        "TIMEOUT": FailureCategory.TIMEOUT,
        "INVALID_STRUCTURED_OUTPUT": FailureCategory.INVALID_STRUCTURED_OUTPUT,
    }
    label = aliases.get(code or "")
    return (
        Attribution(label, "infrastructure", "stable_query_run_failure_code", {"code": code})
        if label is not None
        else None
    )


def _explicit(signals: Iterable[FailureCategory]) -> list[Attribution]:
    stage = {
        FailureCategory.PARSE_MISSING_CONTENT: "parsing",
        FailureCategory.PARSE_READING_ORDER: "parsing",
        FailureCategory.PARSE_TABLE_FAILURE: "parsing",
        FailureCategory.PARSE_ENCODING_FAILURE: "parsing",
        FailureCategory.RERANK_IRRELEVANT_EVIDENCE_PROMOTED: "reranking",
    }
    return [
        Attribution(signal, stage.get(signal, "generation"), "explicit_observable_signal")
        for signal in sorted(signals, key=str)
    ]


def _deduplicate_and_mark_primary(items: list[Attribution]) -> list[Attribution]:
    stage_order = {
        "infrastructure": -1,
        "parsing": 0,
        "retrieval": 1,
        "reranking": 2,
        "context": 3,
        "generation": 4,
        "citation": 5,
    }
    unique = {item.label: item for item in items}
    ordered = sorted(unique.values(), key=lambda item: (stage_order[item.stage], item.label.value))
    return [_primary(item) if index == 0 else item for index, item in enumerate(ordered)]


def _primary(item: Attribution) -> Attribution:
    return Attribution(
        item.label,
        item.stage,
        item.rule,
        item.evidence,
        True,
        item.taxonomy_version,
        item.rules_version,
    )
