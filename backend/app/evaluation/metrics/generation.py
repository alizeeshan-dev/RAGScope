"""Generation and answerability metrics over observable, stored outputs.

The metrics in this module never inspect provider internals.  Human scores are
copied from explicit review labels, answerability metrics compare the stored
answerability decision with a human benchmark label, and the secondary text
similarity metric is a deterministic token-vector cosine.

Infrastructure/provider failures are outside every answer-quality denominator.
For such runs these metrics return ``value=None`` (missing), not zero.  A zero is
only emitted for an observed, evaluable quality failure.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from backend.app.evaluation.contracts import (
    EvaluationMethod,
    MetricOutput,
    MetricScope,
)

AnswerabilityLabel = Literal["answerable", "partially_answerable", "unanswerable"]

GENERATION_METRIC_VERSION = "generation-quality-v1"
ANSWERABILITY_METRIC_VERSION = "answerability-v1"
SEMANTIC_SIMILARITY_VERSION = "token-cosine-v1"


@dataclass(frozen=True, slots=True)
class HumanGenerationLabels:
    """Optional field-level human review labels.

    Scores are normalized to ``[0, 1]``. Claim counts are genuine counts, not
    normalized scores. Missing fields deliberately remain ``None``.
    """

    answer_correctness: float | None = None
    answer_completeness: float | None = None
    partial_answer_accuracy: float | None = None
    false_premise_recognition: float | None = None
    unsupported_claim_count: int | None = None
    contradiction_count: int | None = None
    reviewer_note: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "answer_correctness",
            "answer_completeness",
            "partial_answer_accuracy",
            "false_premise_recognition",
        ):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between zero and one")
        for field_name in ("unsupported_claim_count", "contradiction_count"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")


@dataclass(frozen=True, slots=True)
class AutomaticClaimCounts:
    """Observable automatic claim labels, kept separate from human review."""

    unsupported: int | None = None
    contradicted: int | None = None
    verifier_version: str | None = None

    def __post_init__(self) -> None:
        if self.unsupported is not None and self.unsupported < 0:
            raise ValueError("unsupported cannot be negative")
        if self.contradicted is not None and self.contradicted < 0:
            raise ValueError("contradicted cannot be negative")


@dataclass(frozen=True, slots=True)
class GenerationMetricInputs:
    predicted_answerability: AnswerabilityLabel | None
    answer_text: str | None = None
    reference_answerability: AnswerabilityLabel | None = None
    reference_answer: str | None = None
    human_labels: HumanGenerationLabels = field(default_factory=HumanGenerationLabels)
    automatic_claim_counts: AutomaticClaimCounts = field(default_factory=AutomaticClaimCounts)
    benchmark_question_type: str | None = None
    infrastructure_failure_code: str | None = None

    @property
    def quality_evaluable(self) -> bool:
        """Provider/database/time-out failures are not answer-quality outcomes."""

        return self.infrastructure_failure_code is None


@dataclass(frozen=True, slots=True)
class AutomatedJudgeResult:
    """Typed output of an optional structured answer judge."""

    correctness: float | None
    completeness: float | None
    faithfulness: float | None
    provider_id: str
    model_id: str
    prompt_version: str
    judge_version: str
    raw_details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("correctness", "completeness", "faithfulness"):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between zero and one")


class StructuredAnswerJudge(Protocol):
    """Provider-independent structured judge boundary.

    Production adapters may implement this protocol with Gemini or another
    provider. Ordinary tests use :class:`FakeStructuredAnswerJudge`.
    """

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def judge_version(self) -> str: ...

    def evaluate(
        self,
        *,
        answer: str,
        reference_answer: str | None,
        evidence: Sequence[str],
    ) -> AutomatedJudgeResult: ...


@dataclass(frozen=True, slots=True)
class FakeStructuredAnswerJudge:
    """Deterministic no-network judge for tests and local development."""

    result: AutomatedJudgeResult

    @property
    def provider_id(self) -> str:
        return self.result.provider_id

    @property
    def model_id(self) -> str:
        return self.result.model_id

    @property
    def prompt_version(self) -> str:
        return self.result.prompt_version

    @property
    def judge_version(self) -> str:
        return self.result.judge_version

    def evaluate(
        self,
        *,
        answer: str,
        reference_answer: str | None,
        evidence: Sequence[str],
    ) -> AutomatedJudgeResult:
        del answer, reference_answer, evidence
        return self.result


def _human_output(
    name: str,
    value: float | int | None,
    *,
    details: dict[str, Any] | None = None,
) -> MetricOutput:
    return MetricOutput(
        name=name,
        version=GENERATION_METRIC_VERSION,
        scope=MetricScope.GENERATION,
        value=float(value) if value is not None else None,
        method=EvaluationMethod.HUMAN,
        details={"label_origin": "human_review", **(details or {})},
    )


def _answerability_output(
    name: str,
    value: float | None,
    inputs: GenerationMetricInputs,
    *,
    denominator: str,
) -> MetricOutput:
    return MetricOutput(
        name=name,
        version=ANSWERABILITY_METRIC_VERSION,
        scope=MetricScope.GENERATION,
        value=value,
        method=EvaluationMethod.DETERMINISTIC,
        details={
            "ground_truth_origin": "human_benchmark",
            "predicted_answerability": inputs.predicted_answerability,
            "reference_answerability": inputs.reference_answerability,
            "denominator": denominator,
            "excluded_infrastructure_failure": not inputs.quality_evaluable,
        },
    )


def evaluate_generation_metrics(inputs: GenerationMetricInputs) -> list[MetricOutput]:
    """Evaluate human labels, answerability behavior, and deterministic similarity.

    Per-query rate metrics use applicability denominators:

    * ``correct_abstention_rate``: only human-unanswerable questions;
    * ``incorrect_abstention_rate``: answerable or partially-answerable questions;
    * ``unsupported_answer_rate``: only human-unanswerable questions;
    * ``partial_answer_accuracy``: only when explicitly human-labelled.

    A non-applicable/missing denominator produces ``None``. Consequently, later
    batch code can average only applicable successful runs.
    """

    quality = inputs.quality_evaluable
    human = inputs.human_labels
    human_metrics = [
        _human_output(
            "answer_correctness",
            human.answer_correctness if quality else None,
            details={"excluded_infrastructure_failure": not quality},
        ),
        _human_output(
            "answer_completeness",
            human.answer_completeness if quality else None,
            details={"excluded_infrastructure_failure": not quality},
        ),
        _human_output(
            "partial_answer_accuracy",
            human.partial_answer_accuracy if quality else None,
            details={"excluded_infrastructure_failure": not quality},
        ),
        _human_output(
            "false_premise_recognition",
            human.false_premise_recognition if quality else None,
            details={
                "question_type": inputs.benchmark_question_type,
                "excluded_infrastructure_failure": not quality,
            },
        ),
        _human_output(
            "unsupported_claim_count.human",
            human.unsupported_claim_count if quality else None,
            details={"excluded_infrastructure_failure": not quality},
        ),
        _human_output(
            "contradiction_count.human",
            human.contradiction_count if quality else None,
            details={"excluded_infrastructure_failure": not quality},
        ),
    ]

    auto = inputs.automatic_claim_counts
    automatic_metrics = [
        MetricOutput(
            name="unsupported_claim_count.automatic",
            version=auto.verifier_version or GENERATION_METRIC_VERSION,
            scope=MetricScope.GENERATION,
            value=float(auto.unsupported) if quality and auto.unsupported is not None else None,
            method=EvaluationMethod.AUTOMATED,
            details={"excluded_infrastructure_failure": not quality},
        ),
        MetricOutput(
            name="contradiction_count.automatic",
            version=auto.verifier_version or GENERATION_METRIC_VERSION,
            scope=MetricScope.GENERATION,
            value=float(auto.contradicted) if quality and auto.contradicted is not None else None,
            method=EvaluationMethod.AUTOMATED,
            details={"excluded_infrastructure_failure": not quality},
        ),
    ]

    reference = inputs.reference_answerability
    prediction = inputs.predicted_answerability
    applicable = quality and reference is not None and prediction is not None

    correct_abstention: float | None = None
    incorrect_abstention: float | None = None
    unsupported_answer: float | None = None
    if applicable and reference == "unanswerable":
        correct_abstention = float(prediction == "unanswerable")
        unsupported_answer = float(prediction != "unanswerable")
    elif applicable and reference in {"answerable", "partially_answerable"}:
        incorrect_abstention = float(prediction == "unanswerable")

    answerability_metrics = [
        _answerability_output(
            "correct_abstention_rate",
            correct_abstention,
            inputs,
            denominator="successful human-unanswerable questions",
        ),
        _answerability_output(
            "incorrect_abstention_rate",
            incorrect_abstention,
            inputs,
            denominator="successful human-answerable or partially-answerable questions",
        ),
        _answerability_output(
            "unsupported_answer_rate",
            unsupported_answer,
            inputs,
            denominator="successful human-unanswerable questions",
        ),
    ]

    similarity = deterministic_semantic_similarity(
        inputs.answer_text if quality else None,
        inputs.reference_answer if quality else None,
    )
    similarity_metric = MetricOutput(
        name="semantic_similarity",
        version=SEMANTIC_SIMILARITY_VERSION,
        scope=MetricScope.GENERATION,
        value=similarity,
        method=EvaluationMethod.DETERMINISTIC,
        details={
            "algorithm": "lowercase Unicode word-token term-frequency cosine",
            "excluded_infrastructure_failure": not quality,
        },
    )
    return [*human_metrics, *automatic_metrics, *answerability_metrics, similarity_metric]


def evaluate_with_structured_judge(
    inputs: GenerationMetricInputs,
    *,
    evidence: Sequence[str],
    judge: StructuredAnswerJudge,
) -> list[MetricOutput]:
    """Run an opt-in secondary judge while preserving its complete identity.

    No metric is produced as an evaluable value for infrastructure failures or a
    missing answer. A model judge is always secondary and is identified by
    ``method=model_judge``; callers must not conflate it with human labels.
    """

    if not inputs.quality_evaluable or inputs.answer_text is None:
        result: AutomatedJudgeResult | None = None
    else:
        result = judge.evaluate(
            answer=inputs.answer_text,
            reference_answer=inputs.reference_answer,
            evidence=evidence,
        )
    metadata = {
        "provider_id": judge.provider_id,
        "model_id": judge.model_id,
        "prompt_version": judge.prompt_version,
        "judge_version": judge.judge_version,
        "excluded_infrastructure_failure": not inputs.quality_evaluable,
    }
    values = {
        "model_judged_correctness": result.correctness if result else None,
        "model_judged_completeness": result.completeness if result else None,
        "model_judged_faithfulness": result.faithfulness if result else None,
    }
    if result is not None:
        metadata["raw_details"] = dict(result.raw_details)
    return [
        MetricOutput(
            name=name,
            version=judge.judge_version,
            scope=MetricScope.GENERATION,
            value=value,
            method=EvaluationMethod.MODEL_JUDGE,
            details=dict(metadata),
        )
        for name, value in values.items()
    ]


def deterministic_semantic_similarity(answer: str | None, reference: str | None) -> float | None:
    """Return term-frequency cosine similarity or ``None`` for missing/empty text."""

    if answer is None or reference is None:
        return None
    answer_counts = Counter(_tokens(answer))
    reference_counts = Counter(_tokens(reference))
    if not answer_counts or not reference_counts:
        return None
    dot = sum(count * reference_counts.get(token, 0) for token, count in answer_counts.items())
    answer_norm = math.sqrt(sum(count * count for count in answer_counts.values()))
    reference_norm = math.sqrt(sum(count * count for count in reference_counts.values()))
    return dot / (answer_norm * reference_norm)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
