from __future__ import annotations

import math

import pytest
from backend.app.evaluation.contracts import MetricOutput, MetricScope
from backend.app.evaluation.metrics.retrieval import (
    EVALUATION_METHOD,
    RETRIEVAL_METRIC_VERSION,
    EvidenceGroundTruth,
    RankedChunk,
    RetrievalMetricInputs,
    evaluate_retrieval,
    evidence_set_completeness,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    required_document_recall,
)


@pytest.fixture
def ranking() -> list[RankedChunk]:
    return [
        RankedChunk("distractor", "doc-x", 1),
        RankedChunk("evidence-b", "doc-b", 2),
        RankedChunk("evidence-a", "doc-a", 3),
        RankedChunk("alternative", "doc-c", 4),
    ]


@pytest.fixture
def ground_truth() -> EvidenceGroundTruth:
    return EvidenceGroundTruth(
        acceptable_chunk_sets=(
            frozenset({"evidence-a", "evidence-b"}),
            frozenset({"alternative"}),
        ),
        acceptable_document_sets=(
            frozenset({"doc-a", "doc-b"}),
            frozenset({"doc-c"}),
        ),
    )


def test_recall_at_k_hand_calculation(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    # top-3 finds 2/2 from acceptable set 0, so max(2/2, 0/1) = 1.
    assert recall_at_k(ranking, ground_truth, 3) == 1.0


def test_precision_at_k_hand_calculation(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    # Two of the three slots are valid evidence: 2 / 3.
    assert precision_at_k(ranking, ground_truth, 3) == pytest.approx(2 / 3)


def test_reciprocal_rank_hand_calculation(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    # First relevant evidence is in position 2: 1 / 2.
    assert reciprocal_rank(ranking, ground_truth, 3) == 0.5


def test_ndcg_at_k_hand_calculation(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    # Set 0 gains occur at positions 2 and 3. Ideal gains occur at positions 1 and 2.
    dcg = 1 / math.log2(3) + 1 / math.log2(4)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(ranking, ground_truth, 3) == pytest.approx(dcg / idcg)


def test_evidence_set_completeness_preserves_three_states(
    ground_truth: EvidenceGroundTruth,
) -> None:
    absent = evidence_set_completeness(["distractor"], ground_truth)
    partial = evidence_set_completeness(["evidence-a"], ground_truth)
    complete = evidence_set_completeness(["alternative"], ground_truth)

    assert (absent.value, absent.status) == (0.0, "no_required_evidence")
    assert (partial.value, partial.status) == (0.5, "partial_evidence")
    assert (complete.value, complete.status, complete.matched_set_index) == (
        1.0,
        "complete_set",
        1,
    )


def test_required_document_recall_hand_calculation(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    # top-2 includes doc-b but not doc-a: max(1/2, 0/1) = 1/2.
    assert required_document_recall(ranking, ground_truth, 2) == 0.5


def test_alternative_evidence_set_is_not_penalized(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    alternative_only = [ranking[-1]]
    assert recall_at_k(alternative_only, ground_truth, 1) == 1.0
    assert precision_at_k(alternative_only, ground_truth, 1) == 1.0
    assert ndcg_at_k(alternative_only, ground_truth, 1) == 1.0
    assert evidence_set_completeness(["alternative"], ground_truth).value == 1.0
    assert required_document_recall(alternative_only, ground_truth, 1) == 1.0


def test_missing_ground_truth_is_none_never_zero(ranking: list[RankedChunk]) -> None:
    assert recall_at_k(ranking, None, 3) is None
    assert precision_at_k(ranking, None, 3) is None
    assert reciprocal_rank(ranking, None, 3) is None
    assert ndcg_at_k(ranking, None, 3) is None
    assert evidence_set_completeness(["evidence-a"], None).value is None
    assert required_document_recall(ranking, None, 3) is None

    outputs = evaluate_retrieval(RetrievalMetricInputs(ranking, None, 3))
    assert all(output.value is None for output in outputs)


def test_metric_outputs_are_versioned_and_inspectable(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth
) -> None:
    outputs = evaluate_retrieval(RetrievalMetricInputs(ranking, ground_truth, 3))

    assert len(outputs) == 6
    assert all(isinstance(output, MetricOutput) for output in outputs)
    assert all(output.version == RETRIEVAL_METRIC_VERSION for output in outputs)
    assert all(output.scope == MetricScope.RETRIEVAL for output in outputs)
    assert all(output.method == EVALUATION_METHOD for output in outputs)
    assert all(
        output.details["ranked_chunk_ids"]
        == ["distractor", "evidence-b", "evidence-a"]
        for output in outputs
    )
    assert all("definition" in output.details for output in outputs)


def test_legacy_required_evidence_and_derived_documents_are_supported(
    ranking: list[RankedChunk],
) -> None:
    annotation = EvidenceGroundTruth(
        required_chunk_ids=frozenset({"evidence-a", "evidence-b"}),
        chunk_documents={"evidence-a": "doc-a", "evidence-b": "doc-b"},
    )
    assert recall_at_k(ranking, annotation, 3) == 1.0
    assert required_document_recall(ranking, annotation, 3) == 1.0


@pytest.mark.parametrize("k", [0, -1])
def test_k_must_be_positive(
    ranking: list[RankedChunk], ground_truth: EvidenceGroundTruth, k: int
) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        recall_at_k(ranking, ground_truth, k)
