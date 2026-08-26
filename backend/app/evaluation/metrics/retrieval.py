"""Versioned retrieval metrics over stored rankings and human evidence annotations.

``Recall@k`` is the maximum fraction of required chunks found for any one
acceptable evidence set. ``Precision@k`` is relevant top-k chunks divided by
``k``. ``Reciprocal rank`` is ``1/r`` for the first relevant top-k chunk.
``nDCG@k`` uses binary gain and logarithmic discount and takes the best score
over acceptable sets. ``Evidence-set completeness`` records best acceptable-set
coverage as absent, partial, or complete. ``Required-document recall`` applies
the best-alternative recall rule to document IDs.

Alternative sets are OR alternatives; members within a set are jointly
required. Thus, retrieving one complete valid set earns full recall,
completeness, and nDCG without retrieving every alternative. Missing human
annotation returns ``None``. An annotated miss is an observed zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from backend.app.evaluation.contracts import MetricOutput, MetricScope

RETRIEVAL_METRIC_VERSION: Final = "retrieval-evidence-v1"
# Compatibility names used by context metrics.
METRIC_VERSION: Final = RETRIEVAL_METRIC_VERSION
EVALUATION_METHOD: Final = "deterministic_human_benchmark_evidence"
METRIC_SCOPE: Final = MetricScope.RETRIEVAL

CompletenessStatus = Literal["no_required_evidence", "partial_evidence", "complete_set"]


@dataclass(frozen=True, slots=True)
class RankedChunk:
    """A stored retrieval candidate in observable rank order."""

    chunk_id: str
    document_id: str
    rank: int

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise ValueError("rank must be greater than zero")


@dataclass(frozen=True, slots=True)
class EvidenceGroundTruth:
    """Human evidence references for one benchmark question.

    Entries in an acceptable-set tuple are alternative sufficient sets. Items
    inside one set are jointly required. ``required_*`` fields preserve the
    legacy single-set representation.
    """

    acceptable_chunk_sets: tuple[frozenset[str], ...] = ()
    required_chunk_ids: frozenset[str] = frozenset()
    acceptable_document_sets: tuple[frozenset[str], ...] = ()
    required_document_ids: frozenset[str] = frozenset()
    chunk_documents: Mapping[str, str] = field(default_factory=dict)

    def chunk_sets(self) -> tuple[frozenset[str], ...]:
        sets = tuple(item for item in self.acceptable_chunk_sets if item)
        if sets:
            return sets
        return (self.required_chunk_ids,) if self.required_chunk_ids else ()

    def document_sets(self) -> tuple[frozenset[str], ...]:
        explicit = tuple(item for item in self.acceptable_document_sets if item)
        if explicit:
            return explicit
        if self.required_document_ids:
            return (self.required_document_ids,)
        derived = tuple(
            frozenset(
                self.chunk_documents[chunk_id]
                for chunk_id in chunk_set
                if chunk_id in self.chunk_documents
            )
            for chunk_set in self.chunk_sets()
        )
        return tuple(item for item in derived if item)

    def relevant_chunks(self) -> frozenset[str]:
        sets = self.chunk_sets()
        return frozenset().union(*sets) if sets else frozenset()


@dataclass(frozen=True, slots=True)
class RetrievalMetricInputs:
    """Provider-independent inputs for one stored retrieval result."""

    candidates: Sequence[RankedChunk]
    ground_truth: EvidenceGroundTruth | None
    k: int

    def __post_init__(self) -> None:
        _validate_k(self.k)


@dataclass(frozen=True, slots=True)
class EvidenceCompleteness:
    """Best acceptable-set coverage plus an interpretable three-way state."""

    value: float | None
    status: CompletenessStatus | None
    matched_set_index: int | None
    found_items: int | None
    required_items: int | None


def _validate_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be greater than zero")


def _ranked_unique(candidates: Sequence[RankedChunk], k: int) -> tuple[RankedChunk, ...]:
    _validate_k(k)
    ordered = sorted(candidates, key=lambda candidate: (candidate.rank, candidate.chunk_id))
    result: list[RankedChunk] = []
    seen: set[str] = set()
    for candidate in ordered:
        if candidate.chunk_id in seen:
            continue
        seen.add(candidate.chunk_id)
        result.append(candidate)
        if len(result) == k:
            break
    return tuple(result)


def _ranked_unique_ids(candidates: Sequence[RankedChunk], k: int) -> tuple[str, ...]:
    return tuple(candidate.chunk_id for candidate in _ranked_unique(candidates, k))


def _best_set(
    found: set[str], alternatives: tuple[frozenset[str], ...]
) -> tuple[float, int, int, int] | None:
    if not alternatives:
        return None
    scored = [
        (len(found & required) / len(required), index, len(found & required), len(required))
        for index, required in enumerate(alternatives)
    ]
    return max(scored, key=lambda item: (item[0], -item[1]))


def recall_at_k(
    candidates: Sequence[RankedChunk], ground_truth: EvidenceGroundTruth | None, k: int
) -> float | None:
    """Return ``max_S |top-k intersect S| / |S|`` over acceptable chunk sets."""

    _validate_k(k)
    alternatives = ground_truth.chunk_sets() if ground_truth is not None else ()
    best = _best_set(set(_ranked_unique_ids(candidates, k)), alternatives)
    return best[0] if best is not None else None


def precision_at_k(
    candidates: Sequence[RankedChunk], ground_truth: EvidenceGroundTruth | None, k: int
) -> float | None:
    """Return relevant top-k chunks divided by ``k`` using all valid alternatives."""

    _validate_k(k)
    if ground_truth is None or not ground_truth.chunk_sets():
        return None
    retrieved = _ranked_unique_ids(candidates, k)
    relevant = ground_truth.relevant_chunks()
    return sum(chunk_id in relevant for chunk_id in retrieved) / k


def reciprocal_rank(
    candidates: Sequence[RankedChunk], ground_truth: EvidenceGroundTruth | None, k: int
) -> float | None:
    """Return reciprocal rank of the first acceptable chunk within top-k, else zero."""

    _validate_k(k)
    if ground_truth is None or not ground_truth.chunk_sets():
        return None
    relevant = ground_truth.relevant_chunks()
    for position, chunk_id in enumerate(_ranked_unique_ids(candidates, k), start=1):
        if chunk_id in relevant:
            return 1.0 / position
    return 0.0


def _dcg(ranked_ids: Sequence[str], relevant: frozenset[str]) -> float:
    return sum(
        1.0 / math.log2(position + 1)
        for position, chunk_id in enumerate(ranked_ids, start=1)
        if chunk_id in relevant
    )


def ndcg_at_k(
    candidates: Sequence[RankedChunk], ground_truth: EvidenceGroundTruth | None, k: int
) -> float | None:
    """Return best binary ``DCG@k / IDCG@k`` over acceptable evidence sets.

    Gain is one for a required chunk and zero otherwise. Rank ``i`` has discount
    ``1/log2(i+1)``. Scoring alternatives separately lets any complete valid
    alternative achieve one without requiring all other alternatives.
    """

    _validate_k(k)
    alternatives = ground_truth.chunk_sets() if ground_truth is not None else ()
    if not alternatives:
        return None
    ranked_ids = _ranked_unique_ids(candidates, k)
    scores = []
    for required in alternatives:
        ideal_length = min(k, len(required))
        ideal = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_length + 1))
        scores.append(_dcg(ranked_ids, required) / ideal)
    return max(scores)


def evidence_set_completeness(
    chunk_ids: Sequence[str], ground_truth: EvidenceGroundTruth | None
) -> EvidenceCompleteness:
    """Return best alternative coverage and no/partial/complete evidence state."""

    alternatives = ground_truth.chunk_sets() if ground_truth is not None else ()
    best = _best_set(set(chunk_ids), alternatives)
    if best is None:
        return EvidenceCompleteness(None, None, None, None, None)
    value, index, found_items, required_items = best
    if value == 0.0:
        status: CompletenessStatus = "no_required_evidence"
    elif value == 1.0:
        status = "complete_set"
    else:
        status = "partial_evidence"
    return EvidenceCompleteness(value, status, index, found_items, required_items)


def required_document_recall(
    candidates: Sequence[RankedChunk], ground_truth: EvidenceGroundTruth | None, k: int
) -> float | None:
    """Return best required-document coverage among acceptable document sets."""

    _validate_k(k)
    alternatives = ground_truth.document_sets() if ground_truth is not None else ()
    selected_documents = {candidate.document_id for candidate in _ranked_unique(candidates, k)}
    best = _best_set(selected_documents, alternatives)
    return best[0] if best is not None else None


def _base_details(inputs: RetrievalMetricInputs) -> dict[str, Any]:
    ground_truth = inputs.ground_truth
    return {
        "k": inputs.k,
        "ranked_chunk_ids": list(_ranked_unique_ids(inputs.candidates, inputs.k)),
        "acceptable_chunk_sets": (
            [sorted(item) for item in ground_truth.chunk_sets()] if ground_truth else None
        ),
        "acceptable_document_sets": (
            [sorted(item) for item in ground_truth.document_sets()] if ground_truth else None
        ),
        "ground_truth_origin": "human_benchmark" if ground_truth else None,
    }


def _output(name: str, value: float | None, details: dict[str, Any]) -> MetricOutput:
    return MetricOutput(
        name=name,
        version=RETRIEVAL_METRIC_VERSION,
        scope=MetricScope.RETRIEVAL,
        value=value,
        method=EVALUATION_METHOD,
        details=details,
    )


class RecallAtKMetric:
    name = "recall_at_k"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_evidence_sets", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        alternatives = inputs.ground_truth.chunk_sets() if inputs.ground_truth else ()
        best = _best_set(set(details["ranked_chunk_ids"]), alternatives)
        details.update(
            {
                "definition": "max_S |top-k intersection S| / |S|",
                "matched_set_index": best[1] if best else None,
                "found_required": best[2] if best else None,
                "required": best[3] if best else None,
            }
        )
        return _output(self.name, best[0] if best else None, details)


class PrecisionAtKMetric:
    name = "precision_at_k"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_evidence_sets", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        value = precision_at_k(inputs.candidates, inputs.ground_truth, inputs.k)
        relevant = inputs.ground_truth.relevant_chunks() if inputs.ground_truth else frozenset()
        details.update(
            {
                "definition": "relevant chunks in top-k / k; relevance is evidence-set union",
                "relevant_retrieved": (
                    sum(item in relevant for item in details["ranked_chunk_ids"])
                    if inputs.ground_truth and inputs.ground_truth.chunk_sets()
                    else None
                ),
                "denominator": inputs.k,
            }
        )
        return _output(self.name, value, details)


class ReciprocalRankMetric:
    name = "reciprocal_rank"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_evidence_sets", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        relevant = inputs.ground_truth.relevant_chunks() if inputs.ground_truth else frozenset()
        first_rank = next(
            (
                index
                for index, chunk_id in enumerate(details["ranked_chunk_ids"], start=1)
                if chunk_id in relevant
            ),
            None,
        )
        details.update(
            {
                "definition": "1 / first relevant top-k rank; 0 if no relevant result",
                "first_relevant_rank": first_rank,
            }
        )
        return _output(
            self.name,
            reciprocal_rank(inputs.candidates, inputs.ground_truth, inputs.k),
            details,
        )


class NdcgAtKMetric:
    name = "ndcg_at_k"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_evidence_sets", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        alternatives = inputs.ground_truth.chunk_sets() if inputs.ground_truth else ()
        ranked = details["ranked_chunk_ids"]
        alternative_scores: list[dict[str, float | int]] = []
        for index, required in enumerate(alternatives):
            ideal_length = min(inputs.k, len(required))
            idcg = sum(
                1.0 / math.log2(position + 1) for position in range(1, ideal_length + 1)
            )
            dcg = _dcg(ranked, required)
            alternative_scores.append(
                {"set_index": index, "dcg": dcg, "idcg": idcg, "ndcg": dcg / idcg}
            )
        details.update(
            {
                "definition": "max_S binary DCG@k / ideal DCG@k; discount=1/log2(rank+1)",
                "alternative_scores": alternative_scores,
            }
        )
        value = ndcg_at_k(inputs.candidates, inputs.ground_truth, inputs.k)
        return _output(self.name, value, details)


class EvidenceSetCompletenessMetric:
    name = "evidence_set_completeness"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_evidence_sets", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        result = evidence_set_completeness(details["ranked_chunk_ids"], inputs.ground_truth)
        details.update(
            {
                "definition": "max_S |retrieved intersection S| / |S|",
                "status": result.status,
                "matched_set_index": result.matched_set_index,
                "found_required": result.found_items,
                "required": result.required_items,
            }
        )
        return _output(self.name, result.value, details)


class RequiredDocumentRecallMetric:
    name = "required_document_recall"
    version = RETRIEVAL_METRIC_VERSION
    scope = MetricScope.RETRIEVAL
    required_inputs = ("stored_retrieval_ranking", "human_required_documents", "k")
    method = EVALUATION_METHOD

    def evaluate(self, inputs: RetrievalMetricInputs) -> MetricOutput:
        details = _base_details(inputs)
        ranked = _ranked_unique(inputs.candidates, inputs.k)
        documents = {candidate.document_id for candidate in ranked}
        alternatives = inputs.ground_truth.document_sets() if inputs.ground_truth else ()
        best = _best_set(documents, alternatives)
        details.update(
            {
                "definition": "max_D |top-k documents intersection D| / |D|",
                "retrieved_document_ids": sorted(documents),
                "matched_set_index": best[1] if best else None,
                "found_required": best[2] if best else None,
                "required": best[3] if best else None,
            }
        )
        return _output(self.name, best[0] if best else None, details)


RETRIEVAL_METRICS: Final = (
    RecallAtKMetric(),
    PrecisionAtKMetric(),
    ReciprocalRankMetric(),
    NdcgAtKMetric(),
    EvidenceSetCompletenessMetric(),
    RequiredDocumentRecallMetric(),
)


def evaluate_retrieval(inputs: RetrievalMetricInputs) -> list[MetricOutput]:
    """Evaluate one run; callers can map this pure function across a batch."""

    return [metric.evaluate(inputs) for metric in RETRIEVAL_METRICS]


def evaluate_retrieval_batch(
    batch: Sequence[RetrievalMetricInputs],
) -> list[list[MetricOutput]]:
    """Evaluate multiple runs without aggregating their per-query values."""

    return [evaluate_retrieval(inputs) for inputs in batch]
