from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import (
    BenchmarkQuestion,
    Chunk,
    Citation,
    CitationVerification,
    ClaimSupportStatus,
    ContextSource,
    EvaluationMetricScope,
    EvaluationResult,
    FailureAttribution,
    GeneratedClaim,
    QueryRun,
    QueryRunStatus,
    RetrievalResultRecord,
)
from backend.app.evaluation.attribution import AttributionInputs, attribute_failures
from backend.app.evaluation.citation_verifier import DeterministicCitationVerifier
from backend.app.evaluation.contracts import EvaluationMethod, MetricOutput, MetricScope
from backend.app.evaluation.metrics.citation import (
    CitationClaim,
    CitationJudgment,
    CitationMetricInputs,
    evaluate_citation_metrics,
)
from backend.app.evaluation.metrics.context import (
    ContextItem,
    ContextMetricInputs,
    evaluate_context,
)
from backend.app.evaluation.metrics.generation import (
    AnswerabilityLabel,
    AutomaticClaimCounts,
    GenerationMetricInputs,
    HumanGenerationLabels,
    evaluate_generation_metrics,
)
from backend.app.evaluation.metrics.operational import (
    OperationalMetricInputs,
    evaluate_operational,
)
from backend.app.evaluation.metrics.retrieval import (
    EvidenceGroundTruth,
    RankedChunk,
    RetrievalMetricInputs,
    evaluate_retrieval,
)
from backend.app.evaluation.schemas import HumanMetricCreate
from backend.app.evaluation.taxonomy import FailureCategory
from backend.app.tracing.summary import build_trace_summary


class EvaluationService:
    """Evaluate one stored run; the same pure metric functions are batch-mappable."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def evaluate(
        self, run_id: UUID, *, metric_versions: set[str] | None = None
    ) -> tuple[list[EvaluationResult], list[FailureAttribution]]:
        run = self.get_run(run_id)
        question = (
            self.session.get(BenchmarkQuestion, run.benchmark_question_id)
            if run.benchmark_question_id is not None
            else None
        )
        ground_truth, uuid_sets = self._ground_truth(question, run.corpus_version_id)
        ranked, retrieved_ids, retrieved_documents, reranked_ids = self._ranked(run)
        selected_context, context_items = self._context(run)
        k = self._evaluation_k(run, ranked)

        outputs: list[MetricOutput] = []
        outputs.extend(
            evaluate_retrieval(
                RetrievalMetricInputs(candidates=ranked, ground_truth=ground_truth, k=k)
            )
        )
        context_outputs = evaluate_context(
            ContextMetricInputs(
                selected=context_items,
                acceptable_chunk_sets=uuid_sets,
            )
        )
        outputs.extend(context_outputs)

        claims = list(
            self.session.scalars(
                select(GeneratedClaim)
                .where(GeneratedClaim.query_run_id == run.id)
                .order_by(GeneratedClaim.sequence_number)
            )
        )
        citations = list(
            self.session.scalars(
                select(Citation)
                .where(Citation.query_run_id == run.id)
                .order_by(Citation.citation_id)
            )
        )
        citation_outputs, judgments = self._citation_metrics(
            run, claims, citations, uuid_sets
        )
        outputs.extend(citation_outputs)
        human = self._human_generation_labels(run.id)
        not_evaluated = any(
            claim.support_status is ClaimSupportStatus.NOT_EVALUATED for claim in claims
        )
        automatic_counts = AutomaticClaimCounts(
            unsupported=(
                None
                if not_evaluated
                else sum(claim.support_status is ClaimSupportStatus.UNSUPPORTED for claim in claims)
            ),
            contradicted=(
                None
                if not_evaluated
                else sum(
                    claim.support_status is ClaimSupportStatus.CONTRADICTED for claim in claims
                )
            ),
            verifier_version="citation-lexical-verifier-v1",
        )
        outputs.extend(
            evaluate_generation_metrics(
                GenerationMetricInputs(
                    predicted_answerability=(
                        cast(AnswerabilityLabel, _value(run.answerability_decision))
                        if run.answerability_decision is not None
                        else None
                    ),
                    answer_text=run.answer_text,
                    reference_answerability=(
                        cast(
                            AnswerabilityLabel,
                            _value(question.expected_answerability),
                        )
                        if question is not None
                        else None
                    ),
                    reference_answer=question.reference_answer if question is not None else None,
                    human_labels=human,
                    automatic_claim_counts=automatic_counts,
                    benchmark_question_type=(
                        _value(question.question_type) if question is not None else None
                    ),
                    infrastructure_failure_code=(
                        run.failure_code if run.status is QueryRunStatus.FAILED else None
                    ),
                )
            )
        )
        trace = build_trace_summary(self.session, run.id)
        outputs.extend(
            evaluate_operational(
                OperationalMetricInputs(
                    total_latency_ms=run.total_latency_ms,
                    stage_latency_ms=trace.latency_by_stage_ms,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    estimated_cost=run.estimated_cost,
                    retrieval_calls=self._retrieval_call_count(run),
                    reranking_calls=int(bool(run.route_decision.get("reranking_enabled"))),
                    model_calls=int(run.raw_response_artifact_id is not None),
                    failed=run.status is QueryRunStatus.FAILED,
                    failure_code=run.failure_code,
                )
            )
        )
        if metric_versions is not None:
            outputs = [output for output in outputs if output.version in metric_versions]
        input_snapshot = self._input_snapshot(
            run,
            question,
            ranked,
            selected_context,
            uuid_sets,
            claims,
            judgments,
            human,
        )
        rows = [self._persist_metric(run.id, output, input_snapshot) for output in outputs]
        attributions = self._persist_attributions(
            run=run,
            question=question,
            uuid_sets=uuid_sets,
            retrieved_ids=retrieved_ids,
            retrieved_documents=retrieved_documents,
            reranked_ids=reranked_ids,
            context_ids=selected_context,
            outputs=outputs,
            claims=claims,
            judgments=judgments,
            input_snapshot=input_snapshot,
        )
        self.session.flush()
        return rows, attributions

    def list_results(self, run_id: UUID) -> list[EvaluationResult]:
        self.get_run(run_id)
        return list(
            self.session.scalars(
                select(EvaluationResult)
                .where(EvaluationResult.query_run_id == run_id)
                .order_by(EvaluationResult.metric_scope, EvaluationResult.metric_name)
            )
        )

    def list_attributions(self, run_id: UUID) -> list[FailureAttribution]:
        self.get_run(run_id)
        return list(
            self.session.scalars(
                select(FailureAttribution)
                .where(FailureAttribution.query_run_id == run_id)
                .order_by(FailureAttribution.created_at, FailureAttribution.sequence_number)
            )
        )

    def add_human_metric(
        self, run_id: UUID, payload: HumanMetricCreate
    ) -> EvaluationResult:
        self.get_run(run_id)
        snapshot = {
            "reviewer_label": payload.reviewer_label,
            "reviewer_note": payload.reviewer_note,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        output = MetricOutput(
            name=payload.metric_name,
            version="human-review.v1",
            scope=MetricScope.GENERATION,
            value=payload.metric_value,
            method="human",
            details=snapshot,
        )
        row = self._persist_metric(run_id, output, snapshot)
        self.session.flush()
        return row

    def override_failure(
        self, run_id: UUID, attribution_id: UUID, *, label: str, note: str
    ) -> FailureAttribution:
        attribution = self.session.get(FailureAttribution, attribution_id)
        if attribution is None or attribution.query_run_id != run_id:
            raise LookupError("Failure attribution does not exist for this run")
        if label not in {item.value for item in FailureCategory}:
            raise ValueError("Unknown failure taxonomy label")
        attribution.human_override_label = label
        attribution.human_override_note = note
        attribution.human_reviewed_at = datetime.now(UTC)
        self.session.flush()
        return attribution

    def override_citation(
        self,
        citation_id: UUID,
        *,
        label: str,
        score: float | None,
        note: str,
    ) -> CitationVerification:
        row = self.session.scalar(
            select(CitationVerification)
            .where(CitationVerification.citation_id == citation_id)
            .order_by(CitationVerification.created_at.desc())
        )
        if row is None:
            raise LookupError("Citation verification does not exist")
        row.human_label = label
        row.human_score = score
        row.human_note = note
        row.human_reviewed_at = datetime.now(UTC)
        self.session.flush()
        return row

    def get_run(self, run_id: UUID) -> QueryRun:
        run = self.session.get(QueryRun, run_id)
        if run is None:
            raise LookupError("Query run does not exist")
        return run

    def _ground_truth(
        self, question: BenchmarkQuestion | None, corpus_version_id: UUID
    ) -> tuple[EvidenceGroundTruth | None, tuple[frozenset[UUID], ...] | None]:
        if question is None:
            return None, None
        element_only = {
            reference.element_id
            for evidence_set in question.evidence_sets
            for reference in evidence_set.references
            if reference.chunk_id is None and reference.element_id is not None
        }
        element_chunks: dict[UUID, set[UUID]] = defaultdict(set)
        if element_only:
            for chunk in self.session.scalars(
                select(Chunk).where(Chunk.corpus_version_id == corpus_version_id)
            ):
                for raw_id in chunk.source_element_ids:
                    element_id = UUID(str(raw_id))
                    if element_id in element_only:
                        element_chunks[element_id].add(chunk.id)
        chunk_sets: list[frozenset[UUID]] = []
        document_sets: list[frozenset[str]] = []
        chunk_documents: dict[str, str] = {}
        for evidence_set in question.evidence_sets:
            required = [
                reference
                for reference in evidence_set.references
                if reference.evidence_role in {"required", "alternative"}
            ]
            chunk_ids: set[UUID] = set()
            documents: set[str] = set()
            for reference in required:
                documents.add(str(reference.document_id))
                if reference.chunk_id is not None:
                    chunk_ids.add(reference.chunk_id)
                    chunk_documents[str(reference.chunk_id)] = str(reference.document_id)
                elif reference.element_id is not None:
                    for chunk_id in element_chunks.get(reference.element_id, set()):
                        chunk_ids.add(chunk_id)
                        chunk_documents[str(chunk_id)] = str(reference.document_id)
            if chunk_ids:
                chunk_sets.append(frozenset(chunk_ids))
            if documents:
                document_sets.append(frozenset(documents))
        truth = EvidenceGroundTruth(
            acceptable_chunk_sets=tuple(
                frozenset(str(value) for value in item) for item in chunk_sets
            ),
            required_chunk_ids=frozenset(question.required_chunk_ids),
            acceptable_document_sets=tuple(document_sets),
            required_document_ids=frozenset(question.required_document_ids),
            chunk_documents=chunk_documents,
        )
        return truth, tuple(chunk_sets)

    def _ranked(
        self, run: QueryRun
    ) -> tuple[list[RankedChunk], frozenset[UUID], frozenset[UUID], frozenset[UUID]]:
        rows = self.session.execute(
            select(RetrievalResultRecord, Chunk)
            .join(Chunk, Chunk.id == RetrievalResultRecord.chunk_id)
            .where(RetrievalResultRecord.query_run_id == run.id)
        ).all()
        grouped: dict[UUID, list[tuple[RetrievalResultRecord, Chunk]]] = defaultdict(list)
        for result, chunk in rows:
            grouped[chunk.id].append((result, chunk))
        ranked: list[RankedChunk] = []
        reranked: set[UUID] = set()
        documents: set[UUID] = set()
        for chunk_id, records in grouped.items():
            result_rows = [item[0] for item in records]
            chunk = records[0][1]
            rank = min(
                (item.reranked_rank for item in result_rows if item.reranked_rank is not None),
                default=None,
            )
            if rank is not None:
                reranked.add(chunk_id)
            else:
                rank = min(
                    (item.fused_rank for item in result_rows if item.fused_rank is not None),
                    default=None,
                )
            if rank is None:
                rank = min(
                    (item.original_rank for item in result_rows if item.original_rank is not None),
                    default=2**31,
                )
            documents.add(chunk.document_id)
            ranked.append(RankedChunk(str(chunk_id), str(chunk.document_id), rank))
        ranked.sort(key=lambda item: (item.rank, item.chunk_id))
        return ranked, frozenset(grouped), frozenset(documents), frozenset(reranked)

    def _context(
        self, run: QueryRun
    ) -> tuple[frozenset[UUID], tuple[ContextItem, ...]]:
        rows = self.session.execute(
            select(ContextSource, Chunk)
            .join(Chunk, Chunk.id == ContextSource.chunk_id)
            .where(ContextSource.query_run_id == run.id, ContextSource.selected.is_(True))
            .order_by(ContextSource.sequence_number)
        ).all()
        items = tuple(
            ContextItem(source.chunk_id, source.document_id, chunk.text, source.token_count)
            for source, chunk in rows
        )
        return frozenset(item.chunk_id for item in items), items

    def _citation_metrics(
        self,
        run: QueryRun,
        claims: list[GeneratedClaim],
        citations: list[Citation],
        uuid_sets: tuple[frozenset[UUID], ...] | None,
    ) -> tuple[list[MetricOutput], tuple[CitationJudgment, ...]]:
        context_citations = {
            source.chunk_id: source.citation_id
            for source in self.session.scalars(
                select(ContextSource).where(
                    ContextSource.query_run_id == run.id,
                    ContextSource.selected.is_(True),
                )
            )
            if source.citation_id is not None
        }
        acceptable = (
            tuple(
                frozenset(
                    context_citations[chunk_id]
                    for chunk_id in evidence_set
                    if chunk_id in context_citations
                )
                for evidence_set in uuid_sets
            )
            if uuid_sets
            else None
        )
        if acceptable is not None:
            acceptable = tuple(item for item in acceptable if item) or None
        claim_inputs = tuple(
            CitationClaim(
                claim_id=str(claim.id),
                text=claim.claim_text,
                citation_ids=tuple(claim.citation_ids),
                factual=claim.claim_type == "factual",
                acceptable_citation_sets=acceptable,
            )
            for claim in claims
        )
        sources = {citation.citation_id: citation.referenced_text for citation in citations}
        batch = DeterministicCitationVerifier().verify(claim_inputs, sources)
        rows_by_key = {(str(row.claim_id), row.citation_id): row for row in citations}
        reviewed_judgments: list[CitationJudgment] = []
        for judgment in batch.judgments:
            citation = rows_by_key.get((judgment.claim_id, judgment.citation_id))
            if citation is None:
                continue
            row = self.session.scalar(
                select(CitationVerification).where(
                    CitationVerification.citation_id == citation.id,
                    CitationVerification.method == batch.method,
                    CitationVerification.verifier_version == batch.version,
                )
            )
            if row is None:
                row = CitationVerification(
                    citation_id=citation.id,
                    method=batch.method,
                    verifier_version=batch.version,
                    model_id=None,
                    automatic_label=judgment.automatic_support,
                    automatic_score=judgment.automatic_score,
                    details={
                        **judgment.details,
                        "relevant": judgment.automatic_relevant,
                        "exists": judgment.exists,
                    },
                )
                self.session.add(row)
            citation.entailment_status = judgment.automatic_support
            citation.entailment_score = judgment.automatic_score
            if row.human_label is not None:
                human_support = (
                    "unsupported" if row.human_label == "irrelevant" else row.human_label
                )
                reviewed_judgments.append(
                    judgment.with_human_override(
                        relevant=row.human_label != "irrelevant",
                        support=human_support,  # type: ignore[arg-type]
                        score=row.human_score,
                        note=row.human_note,
                    )
                )
            else:
                reviewed_judgments.append(judgment)
        infrastructure_failure = (
            run.failure_code if run.status is QueryRunStatus.FAILED else None
        )
        metric_inputs = CitationMetricInputs(
            claims=claim_inputs,
            judgments=tuple(reviewed_judgments),
            collective_judgments=batch.collective_judgments,
            infrastructure_failure_code=infrastructure_failure,
        )
        outputs = evaluate_citation_metrics(metric_inputs)
        if any(judgment.human_support is not None for judgment in reviewed_judgments):
            outputs.extend(
                output
                for output in evaluate_citation_metrics(
                    metric_inputs, judgment_source="human"
                )
                if output.method is EvaluationMethod.HUMAN
            )
        self._apply_claim_support(claims, tuple(reviewed_judgments))
        return outputs, tuple(reviewed_judgments)

    @staticmethod
    def _apply_claim_support(
        claims: list[GeneratedClaim], judgments: tuple[CitationJudgment, ...]
    ) -> None:
        by_claim: dict[str, list[str]] = defaultdict(list)
        for judgment in judgments:
            if judgment.exists:
                by_claim[judgment.claim_id].append(judgment.automatic_support)
        for claim in claims:
            if claim.claim_type != "factual":
                continue
            labels = by_claim.get(str(claim.id), [])
            if not claim.citation_ids:
                claim.support_status = ClaimSupportStatus.UNSUPPORTED
            elif "contradicted" in labels:
                claim.support_status = ClaimSupportStatus.CONTRADICTED
            elif "supported" in labels:
                claim.support_status = ClaimSupportStatus.SUPPORTED
            elif "partially_supported" in labels:
                claim.support_status = ClaimSupportStatus.PARTIALLY_SUPPORTED
            elif labels:
                claim.support_status = ClaimSupportStatus.UNSUPPORTED

    def _human_generation_labels(self, run_id: UUID) -> HumanGenerationLabels:
        rows = list(
            self.session.scalars(
                select(EvaluationResult)
                .where(
                    EvaluationResult.query_run_id == run_id,
                    EvaluationResult.evaluation_method == "human",
                )
                .order_by(EvaluationResult.created_at.desc())
            )
        )
        values: dict[str, float | None] = {}
        for row in rows:
            values.setdefault(row.metric_name, row.metric_value)
        return HumanGenerationLabels(
            answer_correctness=values.get("answer_correctness"),
            answer_completeness=values.get("answer_completeness"),
            partial_answer_accuracy=values.get("partial_answer_accuracy"),
            false_premise_recognition=values.get("false_premise_recognition"),
            unsupported_claim_count=_int(values.get("unsupported_claim_count.human")),
            contradiction_count=_int(values.get("contradiction_count.human")),
        )

    @staticmethod
    def _evaluation_k(run: QueryRun, ranked: list[RankedChunk]) -> int:
        candidate_count = run.route_decision.get("candidate_count")
        if isinstance(candidate_count, int) and candidate_count > 0:
            return candidate_count
        return max(len(ranked), 1)

    @staticmethod
    def _retrieval_call_count(run: QueryRun) -> int:
        mode = str(run.route_decision.get("retrieval_mode", "none"))
        return 2 if mode == "hybrid" else int(mode in {"lexical", "dense"})

    @staticmethod
    def _input_snapshot(
        run: QueryRun,
        question: BenchmarkQuestion | None,
        ranked: list[RankedChunk],
        selected_context: frozenset[UUID],
        uuid_sets: tuple[frozenset[UUID], ...] | None,
        claims: list[GeneratedClaim],
        judgments: tuple[CitationJudgment, ...],
        human: HumanGenerationLabels,
    ) -> dict[str, Any]:
        return {
            "query_run_id": str(run.id),
            "benchmark_question_id": str(question.id) if question is not None else None,
            "route_decision": run.route_decision,
            "ranked_chunks": [
                {"chunk_id": item.chunk_id, "document_id": item.document_id, "rank": item.rank}
                for item in ranked
            ],
            "selected_context_chunk_ids": sorted(str(value) for value in selected_context),
            "acceptable_evidence_sets": (
                [sorted(str(value) for value in item) for item in uuid_sets]
                if uuid_sets is not None
                else None
            ),
            "answerability": (
                _value(run.answerability_decision)
                if run.answerability_decision is not None
                else None
            ),
            "expected_answerability": (
                _value(question.expected_answerability) if question is not None else None
            ),
            "answer_text": run.answer_text,
            "reference_answer": question.reference_answer if question is not None else None,
            "claims": [
                {
                    "id": str(claim.id),
                    "text": claim.claim_text,
                    "type": claim.claim_type,
                    "citation_ids": claim.citation_ids,
                    "support_status": _value(claim.support_status),
                }
                for claim in claims
            ],
            "citation_judgments": [
                {
                    "claim_id": judgment.claim_id,
                    "citation_id": judgment.citation_id,
                    "exists": judgment.exists,
                    "automatic_relevant": judgment.automatic_relevant,
                    "automatic_support": judgment.automatic_support,
                    "automatic_score": judgment.automatic_score,
                    "automatic_method": judgment.automatic_method,
                    "automatic_version": judgment.automatic_version,
                    "human_relevant": judgment.human_relevant,
                    "human_support": judgment.human_support,
                    "human_score": judgment.human_score,
                }
                for judgment in judgments
            ],
            "human_generation_labels": {
                "answer_correctness": human.answer_correctness,
                "answer_completeness": human.answer_completeness,
                "partial_answer_accuracy": human.partial_answer_accuracy,
                "false_premise_recognition": human.false_premise_recognition,
                "unsupported_claim_count": human.unsupported_claim_count,
                "contradiction_count": human.contradiction_count,
            },
            "operational": {
                "total_latency_ms": run.total_latency_ms,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "estimated_cost": run.estimated_cost,
            },
            "failure_code": run.failure_code,
        }

    def _persist_metric(
        self, run_id: UUID, output: MetricOutput, input_snapshot: dict[str, Any]
    ) -> EvaluationResult:
        metric_input = {"evaluation_inputs": input_snapshot, "metric_details": output.details}
        input_hash = hashlib.sha256(canonical_json(metric_input)).hexdigest()
        method = str(getattr(output.method, "value", output.method))
        row = self.session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.query_run_id == run_id,
                EvaluationResult.metric_name == output.name,
                EvaluationResult.metric_version == output.version,
                EvaluationResult.evaluation_method == method,
                EvaluationResult.input_hash == input_hash,
            )
        )
        if row is not None:
            return row
        row = EvaluationResult(
            query_run_id=run_id,
            metric_name=output.name,
            metric_scope=EvaluationMetricScope(output.scope.value),
            metric_value=output.value,
            metric_version=output.version,
            evaluation_method=method,
            details=output.details,
            input_snapshot=input_snapshot,
            input_hash=input_hash,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _persist_attributions(
        self,
        *,
        run: QueryRun,
        question: BenchmarkQuestion | None,
        uuid_sets: tuple[frozenset[UUID], ...] | None,
        retrieved_ids: frozenset[UUID],
        retrieved_documents: frozenset[UUID],
        reranked_ids: frozenset[UUID],
        context_ids: frozenset[UUID],
        outputs: list[MetricOutput],
        claims: list[GeneratedClaim],
        judgments: tuple[CitationJudgment, ...],
        input_snapshot: dict[str, Any],
    ) -> list[FailureAttribution]:
        values = {output.name: output.value for output in outputs}
        attributions = attribute_failures(
            AttributionInputs(
                acceptable_chunk_sets=uuid_sets,
                required_document_ids=(
                    frozenset(UUID(value) for value in question.required_document_ids)
                    if question is not None
                    else frozenset()
                ),
                retrieved_chunk_ids=retrieved_ids,
                retrieved_document_ids=retrieved_documents,
                reranked_chunk_ids=reranked_ids,
                reranking_applied=bool(run.route_decision.get("reranking_enabled")),
                context_chunk_ids=context_ids,
                answer_correctness=values.get("answer_correctness"),
                answer_completeness=values.get("answer_completeness"),
                expected_answerable=question.answerable if question is not None else None,
                actual_answerability=(
                    run.answerability_decision.value
                    if run.answerability_decision is not None
                    else None
                ),
                unsupported_claim_count=sum(
                    claim.support_status is ClaimSupportStatus.UNSUPPORTED for claim in claims
                ),
                contradicted_claim_count=sum(
                    claim.support_status is ClaimSupportStatus.CONTRADICTED for claim in claims
                ),
                missing_citation_count=sum(
                    claim.claim_type == "factual" and not claim.citation_ids for claim in claims
                ),
                invalid_citation_count=(
                    sum(not judgment.exists for judgment in judgments)
                    + int(run.failure_code == "INVALID_CITATION")
                ),
                irrelevant_citation_count=sum(
                    judgment.exists and judgment.automatic_relevant is False
                    for judgment in judgments
                ),
                partial_support_count=sum(
                    judgment.automatic_support == "partially_supported"
                    for judgment in judgments
                ),
                redundancy_rate=values.get("context_redundancy_rate"),
                infrastructure_failure_code=run.failure_code,
            )
        )
        attribution_input = {"inputs": input_snapshot, "metrics": values}
        input_hash = hashlib.sha256(canonical_json(attribution_input)).hexdigest()
        existing = list(
            self.session.scalars(
                select(FailureAttribution).where(
                    FailureAttribution.query_run_id == run.id,
                    FailureAttribution.input_hash == input_hash,
                )
            )
        )
        if existing:
            return sorted(existing, key=lambda item: item.sequence_number)
        rows = [
            FailureAttribution(
                query_run_id=run.id,
                sequence_number=index,
                is_primary=item.is_primary,
                pipeline_stage=item.stage,
                automatic_label=item.label.value,
                attribution_rule=item.rule,
                evidence=item.evidence,
                input_hash=input_hash,
                taxonomy_version=item.taxonomy_version,
                rules_version=item.rules_version,
            )
            for index, item in enumerate(attributions, start=1)
        ]
        self.session.add_all(rows)
        return rows


def _int(value: float | None) -> int | None:
    return int(value) if value is not None else None


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)
