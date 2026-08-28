from __future__ import annotations

import math
import typing

import pytest
from backend.app.evaluation.citation_verifier import DeterministicCitationVerifier
from backend.app.evaluation.contracts import EvaluationMethod
from backend.app.evaluation.metrics.citation import (
    CitationClaim,
    CitationMetricInputs,
    evaluate_citation_metrics,
)
from backend.app.evaluation.metrics.generation import (
    AutomatedJudgeResult,
    AutomaticClaimCounts,
    FakeStructuredAnswerJudge,
    GenerationMetricInputs,
    HumanGenerationLabels,
    deterministic_semantic_similarity,
    evaluate_generation_metrics,
    evaluate_with_structured_judge,
)
from backend.app.evaluation.schemas import HumanMetricCreate
from pydantic import ValidationError


def test_human_review_contract_accepts_queue_metrics_and_validates_ranges() -> None:
    for name in (
        "answer_correctness",
        "answer_completeness",
        "appropriate_abstention",
        "false_premise_recognition",
        "claim_support_rate",
        "citation_precision",
    ):
        assert HumanMetricCreate(
            metric_name=name,
            metric_value=1.0,
            reviewer_label="reviewer",
        ).metric_name == name

    with pytest.raises(ValidationError):
        HumanMetricCreate(
            metric_name="answer_correctness",
            metric_value=1.1,
            reviewer_label="reviewer",
        )
    with pytest.raises(ValidationError):
        HumanMetricCreate(
            metric_name="unsupported_claim_count.human",
            metric_value=1.5,
            reviewer_label="reviewer",
        )


def _values(outputs: typing.Sequence[typing.Any]) -> dict[str, float | None]:
    return {output.name: output.value for output in outputs}


def test_human_generation_labels_preserve_correct_and_incomplete_scores() -> None:
    inputs = GenerationMetricInputs(
        predicted_answerability="partially_answerable",
        answer_text="The study included 30 people.",
        reference_answerability="partially_answerable",
        reference_answer="The study included 30 participants and six activities.",
        human_labels=HumanGenerationLabels(
            answer_correctness=1.0,
            answer_completeness=0.5,
            partial_answer_accuracy=0.75,
            unsupported_claim_count=0,
            contradiction_count=0,
        ),
    )

    outputs = evaluate_generation_metrics(inputs)
    values = _values(outputs)

    assert values["answer_correctness"] == 1.0
    assert values["answer_completeness"] == 0.5
    assert values["partial_answer_accuracy"] == 0.75
    assert values["unsupported_claim_count.human"] == 0.0
    assert next(item for item in outputs if item.name == "answer_correctness").method == (
        EvaluationMethod.HUMAN
    )


@pytest.mark.parametrize(
    ("reference", "prediction", "expected"),
    [
        (
            "unanswerable",
            "unanswerable",
            {
                "correct_abstention_rate": 1.0,
                "incorrect_abstention_rate": None,
                "unsupported_answer_rate": 0.0,
            },
        ),
        (
            "unanswerable",
            "answerable",
            {
                "correct_abstention_rate": 0.0,
                "incorrect_abstention_rate": None,
                "unsupported_answer_rate": 1.0,
            },
        ),
        (
            "answerable",
            "unanswerable",
            {
                "correct_abstention_rate": None,
                "incorrect_abstention_rate": 1.0,
                "unsupported_answer_rate": None,
            },
        ),
    ],
)
def test_answerability_denominators(
    reference: str,
    prediction: str,
    expected: dict[str, float | None],
) -> None:
    values = _values(
        evaluate_generation_metrics(
            GenerationMetricInputs(
                predicted_answerability=prediction,  # type: ignore[arg-type]
                reference_answerability=reference,  # type: ignore[arg-type]
            )
        )
    )
    assert {name: values[name] for name in expected} == expected


def test_infrastructure_failure_is_missing_not_zero() -> None:
    outputs = evaluate_generation_metrics(
        GenerationMetricInputs(
            predicted_answerability=None,
            human_labels=HumanGenerationLabels(answer_correctness=0.0),
            infrastructure_failure_code="MODEL_PROVIDER_FAILURE",
        )
    )

    assert all(output.value is None for output in outputs)


def test_unsupported_and_contradicted_counts_keep_automatic_and_human_separate() -> None:
    values = _values(
        evaluate_generation_metrics(
            GenerationMetricInputs(
                predicted_answerability="answerable",
                human_labels=HumanGenerationLabels(
                    unsupported_claim_count=1,
                    contradiction_count=0,
                ),
                automatic_claim_counts=AutomaticClaimCounts(
                    unsupported=2,
                    contradicted=1,
                    verifier_version="claim-verifier-v2",
                ),
            )
        )
    )
    assert values["unsupported_claim_count.human"] == 1.0
    assert values["unsupported_claim_count.automatic"] == 2.0
    assert values["contradiction_count.human"] == 0.0
    assert values["contradiction_count.automatic"] == 1.0


def test_deterministic_similarity_and_fake_structured_judge_are_versioned() -> None:
    assert deterministic_semantic_similarity("alpha beta", "alpha beta") == pytest.approx(1.0)
    assert deterministic_semantic_similarity(None, "alpha") is None

    judge = FakeStructuredAnswerJudge(
        AutomatedJudgeResult(
            correctness=1.0,
            completeness=0.5,
            faithfulness=0.75,
            provider_id="fake",
            model_id="fake-judge",
            prompt_version="judge-prompt-v1",
            judge_version="fake-judge-v3",
            raw_details={"fixture": "incomplete"},
        )
    )
    outputs = evaluate_with_structured_judge(
        GenerationMetricInputs(
            predicted_answerability="partially_answerable",
            answer_text="partial",
            reference_answer="complete",
        ),
        evidence=("source passage",),
        judge=judge,
    )
    assert _values(outputs) == {
        "model_judged_correctness": 1.0,
        "model_judged_completeness": 0.5,
        "model_judged_faithfulness": 0.75,
    }
    assert all(output.version == "fake-judge-v3" for output in outputs)
    assert all(output.method == EvaluationMethod.MODEL_JUDGE for output in outputs)
    assert all(output.details["prompt_version"] == "judge-prompt-v1" for output in outputs)


def _verify(
    claim: CitationClaim,
    sources: dict[str, str],
) -> tuple[CitationMetricInputs, dict[str, float | None]]:
    batch = DeterministicCitationVerifier().verify((claim,), sources)
    inputs = CitationMetricInputs(
        claims=(claim,),
        judgments=batch.judgments,
        collective_judgments=batch.collective_judgments,
    )
    return inputs, _values(evaluate_citation_metrics(inputs))


def test_valid_citation_support() -> None:
    claim = CitationClaim(
        claim_id="C1",
        text="Paris capital France",
        citation_ids=("S1",),
        acceptable_citation_sets=(frozenset({"S1"}),),
    )
    inputs, values = _verify(claim, {"S1": "Paris is the capital of France."})

    assert values == {
        "citation_existence_rate": 1.0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "claim_support_rate": 1.0,
        "citation_completeness": 1.0,
    }
    judgment = inputs.judgments[0]
    assert judgment.automatic_method == "deterministic_token_coverage"
    assert judgment.automatic_version == "citation-lexical-verifier-v1"


def test_missing_citation_is_observed_claim_failure_but_has_no_existence_denominator() -> None:
    claim = CitationClaim(claim_id="C1", text="A factual claim", citation_ids=())
    _, values = _verify(claim, {})

    assert values["citation_existence_rate"] is None
    assert values["citation_precision"] is None
    assert values["claim_support_rate"] == 0.0
    assert values["citation_completeness"] == 0.0


def test_invalid_citation_id_fails_existence_precision_and_support() -> None:
    claim = CitationClaim(claim_id="C1", text="Paris capital France", citation_ids=("S9",))
    _, values = _verify(claim, {"S1": "Paris is the capital of France."})

    assert values["citation_existence_rate"] == 0.0
    assert values["citation_precision"] == 0.0
    assert values["claim_support_rate"] == 0.0
    assert values["citation_completeness"] == 0.0


def test_irrelevant_citation_exists_but_is_not_support() -> None:
    claim = CitationClaim(claim_id="C1", text="Paris capital France", citation_ids=("S1",))
    _, values = _verify(claim, {"S1": "Neural networks classify medical images."})

    assert values["citation_existence_rate"] == 1.0
    assert values["citation_precision"] == 0.0
    assert values["claim_support_rate"] == 0.0
    assert values["citation_completeness"] == 0.0


def test_partial_citation_is_relevant_but_not_full_claim_support() -> None:
    claim = CitationClaim(
        claim_id="C1",
        text="Paris capital France Europe",
        citation_ids=("S1",),
    )
    inputs, values = _verify(claim, {"S1": "Paris is in France."})

    assert inputs.judgments[0].automatic_support == "partially_supported"
    assert values["citation_precision"] == 1.0
    assert values["claim_support_rate"] == 0.0
    assert values["citation_completeness"] == 0.0


def test_alternative_expected_sets_use_best_valid_citation_recall() -> None:
    claim = CitationClaim(
        claim_id="C1",
        text="Paris capital France",
        citation_ids=("S3",),
        acceptable_citation_sets=(
            frozenset({"S1", "S2"}),
            frozenset({"S3"}),
        ),
    )
    _, values = _verify(claim, {"S3": "Paris is the capital of France."})
    assert values["citation_recall"] == 1.0


def test_human_override_preserves_automatic_judgment() -> None:
    claim = CitationClaim(claim_id="C1", text="Paris capital France", citation_ids=("S1",))
    batch = DeterministicCitationVerifier().verify(
        (claim,), {"S1": "Neural networks classify images."}
    )
    automatic = batch.judgments[0]
    reviewed = automatic.with_human_override(
        relevant=True,
        support="supported",
        score=1.0,
        reviewer="reviewer-1",
        note="The lexical baseline missed a paraphrase.",
    )
    reviewed_collective = batch.collective_judgments[0].with_human_override(
        supported=True,
        score=1.0,
        reviewer="reviewer-1",
    )
    inputs = CitationMetricInputs(
        claims=(claim,),
        judgments=(reviewed,),
        collective_judgments=(reviewed_collective,),
    )

    automatic_values = _values(evaluate_citation_metrics(inputs, judgment_source="automatic"))
    human_values = _values(evaluate_citation_metrics(inputs, judgment_source="human"))

    assert reviewed.automatic_relevant is False
    assert reviewed.automatic_support == "unsupported"
    assert automatic_values["citation_precision"] == 0.0
    assert automatic_values["claim_support_rate"] == 0.0
    assert human_values["citation_precision"] == 1.0
    assert human_values["claim_support_rate"] == 1.0
    assert human_values["citation_completeness"] == 1.0


def test_missing_human_labels_remain_missing() -> None:
    claim = CitationClaim(claim_id="C1", text="Paris capital France", citation_ids=("S1",))
    inputs, _ = _verify(claim, {"S1": "Paris is the capital of France."})
    values = _values(evaluate_citation_metrics(inputs, judgment_source="human"))

    assert values["citation_precision"] is None
    assert values["claim_support_rate"] is None
    assert values["citation_completeness"] is None


def test_citation_infrastructure_failure_is_excluded() -> None:
    claim = CitationClaim(claim_id="C1", text="Paris capital France", citation_ids=("S1",))
    batch = DeterministicCitationVerifier().verify(
        (claim,), {"S1": "Paris is the capital of France."}
    )
    outputs = evaluate_citation_metrics(
        CitationMetricInputs(
            claims=(claim,),
            judgments=batch.judgments,
            collective_judgments=batch.collective_judgments,
            infrastructure_failure_code="MODEL_PROVIDER_FAILURE",
        )
    )
    assert all(output.value is None for output in outputs)


def test_similarity_uses_term_frequency_cosine() -> None:
    expected = 1.0 / math.sqrt(2.0)
    assert deterministic_semantic_similarity("alpha alpha", "alpha beta") == pytest.approx(expected)
