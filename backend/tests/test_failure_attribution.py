from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.evaluation.attribution import AttributionInputs, attribute_failures
from backend.app.evaluation.taxonomy import FailureCategory


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("retrieval", FailureCategory.RETRIEVAL_TOTAL_MISS),
        ("reranking", FailureCategory.RERANK_REQUIRED_EVIDENCE_DEMOTED),
        ("context", FailureCategory.CONTEXT_REQUIRED_EVIDENCE_DROPPED),
        ("generation", FailureCategory.GENERATION_IGNORED_EVIDENCE),
    ],
)
def test_evidence_loss_is_attributed_to_first_responsible_stage(
    stage: str, expected: FailureCategory
) -> None:
    required = uuid4()
    retrieved = frozenset() if stage == "retrieval" else frozenset({required})
    reranked = (
        frozenset()
        if stage == "reranking"
        else (retrieved if stage == "retrieval" else frozenset({required}))
    )
    context = (
        frozenset()
        if stage == "context"
        else (reranked if stage in {"retrieval", "reranking"} else frozenset({required}))
    )
    results = attribute_failures(
        AttributionInputs(
            acceptable_chunk_sets=(frozenset({required}),),
            retrieved_chunk_ids=retrieved,
            reranked_chunk_ids=reranked,
            reranking_applied=stage in {"reranking", "context", "generation"},
            context_chunk_ids=context,
            answer_correctness=0.0 if stage == "generation" else None,
        )
    )
    assert results[0].label is expected
    assert results[0].is_primary


def test_infrastructure_failure_is_not_quality_attribution() -> None:
    result = attribute_failures(
        AttributionInputs(
            infrastructure_failure_code="TIMEOUT",
            answer_correctness=0.0,
            missing_citation_count=1,
        )
    )
    assert [item.label for item in result] == [FailureCategory.TIMEOUT]


def test_parsing_signal_and_citation_secondary_are_preserved() -> None:
    result = attribute_failures(
        AttributionInputs(
            explicit_signals=frozenset({FailureCategory.PARSE_TABLE_FAILURE}),
            missing_citation_count=2,
        )
    )
    assert result[0].label is FailureCategory.PARSE_TABLE_FAILURE
    assert result[1].label is FailureCategory.CITATION_MISSING


def test_answerability_failures_are_distinct() -> None:
    failed_to_abstain = attribute_failures(
        AttributionInputs(expected_answerable=False, actual_answerability="answerable")
    )
    incorrect_abstention = attribute_failures(
        AttributionInputs(expected_answerable=True, actual_answerability="unanswerable")
    )
    assert failed_to_abstain[0].label is FailureCategory.GENERATION_FAILED_TO_ABSTAIN
    assert incorrect_abstention[0].label is FailureCategory.GENERATION_INCORRECT_ABSTENTION
