from __future__ import annotations

import hashlib
import importlib.util
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import (
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    BenchmarkVersion,
    CorpusVersion,
    Experiment,
    ExperimentRun,
    ExperimentRunAttempt,
    PipelineConfiguration,
    PipelineExecutionMode,
    PromptTemplate,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RouterConfiguration,
)
from backend.app.evaluation.metrics.citation import CITATION_METRIC_VERSION
from backend.app.evaluation.metrics.context import CONTEXT_METRIC_VERSION
from backend.app.evaluation.metrics.generation import (
    ANSWERABILITY_METRIC_VERSION,
    GENERATION_METRIC_VERSION,
    SEMANTIC_SIMILARITY_VERSION,
)
from backend.app.evaluation.metrics.operational import OPERATIONAL_METRIC_VERSION
from backend.app.evaluation.metrics.retrieval import RETRIEVAL_METRIC_VERSION
from backend.app.evaluation.taxonomy import (
    ATTRIBUTION_RULES_VERSION,
    FAILURE_TAXONOMY_VERSION,
)
from backend.app.prompts.service import GROUNDED_ANSWER_V1, PromptRegistry
from backend.app.tracing.redaction import configured_sensitive_values, redact

from .schemas import (
    ExperimentAnalysisConfiguration,
    ExperimentCostEstimate,
    ExperimentDependencySnapshot,
    ExperimentRecord,
    ExperimentRunCell,
    PipelineDependencySnapshot,
    PricingSnapshot,
    QueryExecutionOutcome,
    QuestionSnapshot,
    RetryPolicy,
)
from .schemas import (
    ExperimentRunAttempt as ExperimentRunAttemptRecord,
)


class SQLAlchemyExperimentStore:
    """Durable adapter; each state write commits before provider work can start."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_experiment(self, experiment: ExperimentRecord) -> None:
        self.session.add(
            Experiment(
                id=experiment.id,
                name=experiment.name,
                research_question=experiment.research_question,
                corpus_version_id=experiment.corpus_version_id,
                benchmark_version_id=experiment.benchmark_version_id,
                pipeline_configuration_ids=[
                    str(value) for value in experiment.pipeline_configuration_ids
                ],
                repetitions=experiment.repetitions,
                status=experiment.status,
                code_commit=experiment.code_commit,
                stop_on_error=experiment.stop_on_error,
                retry_policy=experiment.retry_policy.model_dump(mode="json"),
                analysis_configuration=experiment.analysis_configuration.model_dump(mode="json"),
                dependency_snapshot=experiment.dependency_snapshot.model_dump(mode="json"),
                configuration_hash=experiment.configuration_hash,
                frozen_at=experiment.frozen_at,
                started_at=experiment.started_at,
                completed_at=experiment.completed_at,
            )
        )
        self.session.commit()

    def get_experiment(self, experiment_id: UUID) -> ExperimentRecord | None:
        row = self.session.get(Experiment, experiment_id)
        return _experiment_record(row) if row is not None else None

    def save_experiment(self, experiment: ExperimentRecord) -> None:
        row = self.session.get(Experiment, experiment.id)
        if row is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        row.status = experiment.status
        row.analysis_configuration = experiment.analysis_configuration.model_dump(mode="json")
        row.dependency_snapshot = experiment.dependency_snapshot.model_dump(mode="json")
        row.configuration_hash = experiment.configuration_hash
        row.frozen_at = experiment.frozen_at
        row.started_at = experiment.started_at
        row.completed_at = experiment.completed_at
        self.session.commit()

    def save_cost_estimate(self, estimate: ExperimentCostEstimate) -> None:
        row = self.session.get(Experiment, estimate.experiment_id)
        if row is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        row.cost_estimate = estimate.model_dump(mode="json")
        row.estimated_cost = estimate.maximum_expected_cost
        row.estimated_cost_currency = estimate.currency
        row.cost_fully_configured = estimate.cost_fully_configured
        self.session.commit()

    def add_run_cells(self, cells: tuple[ExperimentRunCell, ...]) -> None:
        for cell in cells:
            self.session.add(
                ExperimentRun(
                    id=cell.id,
                    experiment_id=cell.experiment_id,
                    benchmark_question_id=cell.benchmark_question_id,
                    pipeline_configuration_id=cell.pipeline_configuration_id,
                    repetition_index=cell.repetition_index,
                    query_text=cell.query_text,
                    status=cell.status,
                    idempotency_key=cell.idempotency_key,
                    random_seed=cell.random_seed,
                    attempt_count=cell.attempt_count,
                    max_attempts=cell.max_attempts,
                    last_failure_code=cell.last_failure_code,
                    last_failure_message=cell.last_failure_message,
                    started_at=cell.started_at,
                    finished_at=cell.finished_at,
                )
            )
        self.session.commit()

    def list_run_cells(self, experiment_id: UUID) -> list[ExperimentRunCell]:
        rows = self.session.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_id == experiment_id)
            .order_by(
                ExperimentRun.benchmark_question_id,
                ExperimentRun.pipeline_configuration_id,
                ExperimentRun.repetition_index,
            )
        ).all()
        return [_run_cell(row) for row in rows]

    def save_run_cell(self, cell: ExperimentRunCell) -> None:
        row = self.session.get(ExperimentRun, cell.id)
        if row is None:
            raise DomainError("EXPERIMENT_RUN_NOT_FOUND", "Experiment run not found.")
        row.status = cell.status
        row.attempt_count = cell.attempt_count
        row.last_failure_code = cell.last_failure_code
        row.last_failure_message = cell.last_failure_message
        row.started_at = cell.started_at
        row.finished_at = cell.finished_at
        self.session.commit()

    def add_attempt(self, attempt: ExperimentRunAttemptRecord) -> None:
        self.session.add(
            ExperimentRunAttempt(
                experiment_run_id=attempt.experiment_run_id,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                query_run_id=attempt.query_run_id,
                failure_code=attempt.failure_code,
                failure_message=attempt.failure_message,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
            )
        )
        self.session.commit()

    def save_attempt(self, attempt: ExperimentRunAttemptRecord) -> None:
        row = self.session.scalar(
            select(ExperimentRunAttempt).where(
                ExperimentRunAttempt.experiment_run_id == attempt.experiment_run_id,
                ExperimentRunAttempt.attempt_number == attempt.attempt_number,
            )
        )
        if row is None:
            raise DomainError("EXPERIMENT_ATTEMPT_NOT_FOUND", "Experiment attempt not found.")
        row.status = attempt.status
        row.query_run_id = attempt.query_run_id
        row.failure_code = attempt.failure_code
        row.failure_message = attempt.failure_message
        row.finished_at = attempt.finished_at
        self.session.commit()

    def get_attempt(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> ExperimentRunAttemptRecord | None:
        row = self.session.scalar(
            select(ExperimentRunAttempt).where(
                ExperimentRunAttempt.experiment_run_id == experiment_run_id,
                ExperimentRunAttempt.attempt_number == attempt_number,
            )
        )
        return _attempt_record(row) if row is not None else None

    def find_attempt_outcome(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> QueryExecutionOutcome | None:
        attempt = self.session.scalar(
            select(ExperimentRunAttempt).where(
                ExperimentRunAttempt.experiment_run_id == experiment_run_id,
                ExperimentRunAttempt.attempt_number == attempt_number,
            )
        )
        if attempt is None or attempt.query_run_id is None:
            run = self.session.scalar(
                select(QueryRun).where(
                    QueryRun.experiment_run_id == experiment_run_id,
                    QueryRun.experiment_attempt_number == attempt_number,
                )
            )
        else:
            run = self.session.get(QueryRun, attempt.query_run_id)
        if run is None or run.status not in {QueryRunStatus.SUCCEEDED, QueryRunStatus.FAILED}:
            return None
        return QueryExecutionOutcome(
            query_run_id=run.id,
            succeeded=run.status is QueryRunStatus.SUCCEEDED,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
        )


class SQLAlchemyExperimentDependencyResolver:
    """Resolve exact frozen dependency snapshots without provider calls."""

    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def resolve(
        self,
        *,
        corpus_version_id: UUID,
        benchmark_version_id: UUID,
        pipeline_configuration_ids: tuple[UUID, ...],
    ) -> ExperimentDependencySnapshot:
        corpus = self.session.get(CorpusVersion, corpus_version_id)
        if corpus is None:
            raise DomainError(
                "CORPUS_VERSION_NOT_FOUND", "Corpus version not found.", status_code=404
            )
        if corpus.content_hash is None:
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "Corpus version does not have a reproducibility content hash.",
            )
        benchmark = self.session.scalar(
            select(BenchmarkVersion)
            .where(BenchmarkVersion.id == benchmark_version_id)
            .options(
                selectinload(BenchmarkVersion.questions)
                .selectinload(BenchmarkQuestion.evidence_sets)
                .selectinload(BenchmarkEvidenceSet.references)
            )
        )
        if benchmark is None:
            raise DomainError(
                "BENCHMARK_VERSION_NOT_FOUND", "Benchmark version not found.", status_code=404
            )
        pipelines = tuple(self._pipeline(value, corpus) for value in pipeline_configuration_ids)
        questions = tuple(
            QuestionSnapshot(
                id=question.id,
                question_text=question.question_text,
                annotation_status=_enum_value(question.annotation_status),
                question_type=_enum_value(question.question_type),
                difficulty=_enum_value(question.difficulty),
                expected_answerability=_enum_value(question.expected_answerability),
                reference_answer=question.reference_answer,
                answer_criteria=question.answer_criteria,
            )
            for question in sorted(benchmark.questions, key=lambda value: str(value.id))
        )
        return ExperimentDependencySnapshot(
            corpus_version_id=corpus.id,
            corpus_status=corpus.status.value,
            corpus_frozen=corpus.is_frozen,
            corpus_content_hash=corpus.content_hash,
            benchmark_version_id=benchmark.id,
            benchmark_corpus_version_id=benchmark.corpus_version_id,
            benchmark_status=benchmark.status.value,
            benchmark_frozen=benchmark.frozen_at is not None,
            benchmark_snapshot_hash=_benchmark_hash(benchmark),
            questions=questions,
            pipelines=pipelines,
            metric_versions={
                "retrieval": RETRIEVAL_METRIC_VERSION,
                "context": CONTEXT_METRIC_VERSION,
                "generation": GENERATION_METRIC_VERSION,
                "answerability": ANSWERABILITY_METRIC_VERSION,
                "semantic_similarity": SEMANTIC_SIMILARITY_VERSION,
                "citation": CITATION_METRIC_VERSION,
                "operational": OPERATIONAL_METRIC_VERSION,
            },
            citation_verifier_version="citation-lexical-verifier-v1",
            failure_taxonomy_version=FAILURE_TAXONOMY_VERSION,
            attribution_rules_version=ATTRIBUTION_RULES_VERSION,
        )

    def _pipeline(
        self, pipeline_id: UUID, corpus: CorpusVersion
    ) -> PipelineDependencySnapshot:
        pipeline = self.session.get(PipelineConfiguration, pipeline_id)
        if pipeline is None:
            raise DomainError(
                "PIPELINE_CONFIGURATION_NOT_FOUND",
                "Pipeline configuration not found.",
                status_code=404,
            )
        generation = pipeline.generation_configuration
        context = pipeline.context_configuration
        reranker = pipeline.reranker_configuration
        adaptive = pipeline.adaptive_configuration
        adaptive_mode = pipeline.execution_mode is PipelineExecutionMode.ADAPTIVE
        router = (
            self.session.get(RouterConfiguration, pipeline.router_configuration_id)
            if pipeline.router_configuration_id is not None
            else None
        )
        retrieval_mode = _maximum_retrieval_mode(pipeline)
        embedding = corpus.embedding_configuration
        generation_provider = str(generation.get("provider", "fake"))
        embedding_provider = str(embedding.get("provider", self.settings.embedding_provider))
        reranker_provider = str(reranker.get("provider", "fake"))
        reranking_enabled = (
            bool(adaptive.get("allow_reranking", True))
            if adaptive_mode
            else bool(reranker.get("enabled", False))
        )
        unavailable_capabilities: list[str] = []
        if generation_provider == "gemini" and self.settings.gemini_api_key is None:
            unavailable_capabilities.append("generation:gemini")
        elif generation_provider in {"openai_compatible", "openai-compatible"} and (
            self.settings.generation_api_key is None
        ):
            unavailable_capabilities.append("generation:openai-compatible")
        elif generation_provider not in {
            "fake",
            "gemini",
            "openai_compatible",
            "openai-compatible",
        }:
            unavailable_capabilities.append(f"generation:{generation_provider}")
        if embedding_provider == "gemini" and self.settings.gemini_api_key is None:
            unavailable_capabilities.append("embedding:gemini")
        elif embedding_provider not in {"fake", "gemini"}:
            unavailable_capabilities.append(f"embedding:{embedding_provider}")
        if reranking_enabled and reranker_provider in {
            "sentence_transformers_cross_encoder",
            "sentence-transformers-cross-encoder",
        }:
            if importlib.util.find_spec("sentence_transformers") is None:
                unavailable_capabilities.append("reranker:sentence-transformers-not-installed")
            if reranker.get("model_revision") is None:
                unavailable_capabilities.append("reranker:model-revision-not-pinned")
        elif reranking_enabled and reranker_provider != "fake":
            unavailable_capabilities.append(f"reranker:{reranker_provider}")
        maximum_context = int(context.get("token_budget", 2_048))
        if adaptive_mode:
            maximum_context = min(
                int(adaptive.get("maximum_context_budget", maximum_context)),
                _router_maximum_context(router, fallback=maximum_context),
            )
        return PipelineDependencySnapshot(
            id=pipeline.id,
            name=pipeline.name,
            version=pipeline.version,
            frozen=pipeline.is_frozen,
            execution_mode=pipeline.execution_mode,
            configuration_hash=pipeline.configuration_hash,
            configuration_snapshot=redact(
                {
                    "execution_mode": pipeline.execution_mode.value,
                    "retrieval_mode": pipeline.retrieval_mode.value,
                    "lexical": pipeline.lexical_configuration,
                    "dense": pipeline.dense_configuration,
                    "fusion": pipeline.fusion_configuration,
                    "reranker": pipeline.reranker_configuration,
                    "query_processing": pipeline.query_processing_configuration,
                    "context": pipeline.context_configuration,
                    "generation": pipeline.generation_configuration,
                    "citation": pipeline.citation_configuration,
                    "prompt_versions": pipeline.prompt_versions,
                    "adaptive": pipeline.adaptive_configuration,
                    "router": (
                        {
                            "id": str(router.id),
                            "version": router.version,
                            "router_version": router.router_version,
                            "classifier_version": router.classifier_version,
                            "configuration": router.configuration,
                            "configuration_hash": router.configuration_hash,
                        }
                        if router is not None
                        else None
                    ),
                },
                sensitive_values=configured_sensitive_values(),
            ),
            index_snapshots=tuple(
                {
                    "id": str(index.id),
                    "index_type": index.index_type.value,
                    "status": index.status.value,
                    "configuration": redact(
                        index.configuration,
                        sensitive_values=configured_sensitive_values(),
                    ),
                    "configuration_hash": index.configuration_hash,
                    "provider_id": index.provider_id,
                    "model_id": index.model_id,
                    "embedding_dimension": index.embedding_dimension,
                    "preprocessing_version": index.preprocessing_version,
                    "similarity_method": index.similarity_method,
                }
                for index in sorted(
                    corpus.indexes,
                    key=lambda value: (value.index_type.value, str(value.id)),
                )
            ),
            prompt_snapshot=_prompt_snapshot(self.session, pipeline.prompt_versions),
            generation_provider=generation_provider,
            generation_model=str(generation.get("model", "unknown")),
            retrieval_mode=retrieval_mode,
            reranking_enabled=reranking_enabled,
            maximum_context_tokens=maximum_context,
            maximum_output_tokens=int(generation.get("max_output_tokens", 512)),
            pricing=PricingSnapshot(
                generation_billable=generation_provider != "fake",
                generation_input_per_million_tokens=_optional_float(
                    generation.get("input_price_per_million_tokens")
                    if generation.get("input_price_per_million_tokens") is not None
                    else self.settings.generation_input_price_per_million
                ),
                generation_output_per_million_tokens=_optional_float(
                    generation.get("output_price_per_million_tokens")
                    if generation.get("output_price_per_million_tokens") is not None
                    else self.settings.generation_output_price_per_million
                ),
                embedding_billable=(
                    retrieval_mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}
                    and embedding_provider != "fake"
                ),
                embedding_input_per_million_tokens=_optional_float(
                    embedding.get("input_price_per_million_tokens")
                    if embedding.get("input_price_per_million_tokens") is not None
                    else self.settings.embedding_input_price_per_million
                ),
                reranker_billable=(
                    reranker_provider != "fake"
                    and reranking_enabled
                ),
                reranker_per_call=_optional_float(reranker.get("price_per_call")),
            ),
            adaptive_router_configuration_hash=(
                router.configuration_hash if router is not None else None
            ),
            adaptive_router_frozen=(router.is_frozen if router is not None else None),
            providers_available=not unavailable_capabilities,
            unavailable_capabilities=tuple(sorted(unavailable_capabilities)),
        )


def _experiment_record(row: Experiment) -> ExperimentRecord:
    return ExperimentRecord(
        id=row.id,
        name=row.name,
        research_question=row.research_question,
        corpus_version_id=row.corpus_version_id,
        benchmark_version_id=row.benchmark_version_id,
        pipeline_configuration_ids=tuple(UUID(value) for value in row.pipeline_configuration_ids),
        repetitions=row.repetitions,
        code_commit=row.code_commit,
        stop_on_error=row.stop_on_error,
        retry_policy=RetryPolicy.model_validate(row.retry_policy),
        analysis_configuration=ExperimentAnalysisConfiguration.model_validate(
            row.analysis_configuration or {}
        ),
        status=row.status,
        dependency_snapshot=ExperimentDependencySnapshot.model_validate(row.dependency_snapshot),
        configuration_hash=row.configuration_hash,
        created_at=row.created_at,
        frozen_at=row.frozen_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _run_cell(row: ExperimentRun) -> ExperimentRunCell:
    latest_attempt = max(row.attempts, key=lambda value: value.attempt_number, default=None)
    query_run_id = latest_attempt.query_run_id if latest_attempt is not None else None
    return ExperimentRunCell(
        id=row.id,
        experiment_id=row.experiment_id,
        benchmark_question_id=row.benchmark_question_id,
        pipeline_configuration_id=row.pipeline_configuration_id,
        repetition_index=row.repetition_index,
        query_text=row.query_text,
        status=row.status,
        idempotency_key=row.idempotency_key,
        random_seed=row.random_seed,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        query_run_id=query_run_id,
        last_failure_code=row.last_failure_code,
        last_failure_message=row.last_failure_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _attempt_record(row: ExperimentRunAttempt) -> ExperimentRunAttemptRecord:
    return ExperimentRunAttemptRecord(
        experiment_run_id=row.experiment_run_id,
        attempt_number=row.attempt_number,
        status=row.status,
        query_run_id=row.query_run_id,
        failure_code=row.failure_code,
        failure_message=row.failure_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _benchmark_hash(benchmark: BenchmarkVersion) -> str:
    questions: list[dict[str, Any]] = []
    for question in sorted(benchmark.questions, key=lambda value: str(value.id)):
        evidence_sets = []
        for evidence_set in sorted(question.evidence_sets, key=lambda value: value.set_number):
            references = [
                {
                    "id": str(reference.id),
                    "document_id": str(reference.document_id),
                    "element_id": str(reference.element_id) if reference.element_id else None,
                    "chunk_id": str(reference.chunk_id) if reference.chunk_id else None,
                    "page_number": reference.page_number,
                    "selected_text": reference.selected_text,
                    "evidence_role": reference.evidence_role,
                }
                for reference in sorted(evidence_set.references, key=lambda value: str(value.id))
            ]
            evidence_sets.append(
                {
                    "id": str(evidence_set.id),
                    "set_number": evidence_set.set_number,
                    "description": evidence_set.description,
                    "references": references,
                }
            )
        questions.append(
            {
                "id": str(question.id),
                "question_text": question.question_text,
                "question_type": _enum_value(question.question_type),
                "difficulty": _enum_value(question.difficulty),
                "answerable": question.answerable,
                "expected_answerability": _enum_value(question.expected_answerability),
                "reference_answer": question.reference_answer,
                "answer_criteria": question.answer_criteria,
                "unanswerable_explanation": question.unanswerable_explanation,
                "tags": question.tags,
                "annotation_status": _enum_value(question.annotation_status),
                "evidence_sets": evidence_sets,
            }
        )
    payload = {
        "schema_version": "benchmark-experiment-snapshot-v1",
        "benchmark_version_id": str(benchmark.id),
        "corpus_version_id": str(benchmark.corpus_version_id),
        "version": benchmark.version,
        "questions": questions,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _prompt_snapshot(session: Session, prompt_versions: dict[str, Any]) -> dict[str, Any]:
    default_prompt = PromptRegistry(session).ensure_grounded_prompt()
    references = dict(prompt_versions)
    references.setdefault(
        "grounded_generation",
        {
            "prompt_id": GROUNDED_ANSWER_V1.prompt_id,
            "version": GROUNDED_ANSWER_V1.version,
        },
    )
    result: dict[str, Any] = {"references": references, "resolved": {}}
    for name, reference in references.items():
        if not isinstance(reference, dict):
            continue
        prompt_id = reference.get("prompt_id")
        version = reference.get("version")
        if not isinstance(prompt_id, str) or not isinstance(version, int):
            continue
        prompt = session.scalar(
            select(PromptTemplate).where(
                PromptTemplate.prompt_id == prompt_id,
                PromptTemplate.version == version,
            )
        )
        if prompt is not None:
            result["resolved"][name] = {
                "id": str(prompt.id),
                "content_hash": prompt.content_hash,
                "frozen": prompt.is_frozen,
            }
    result["runtime_grounded_prompt_id"] = str(default_prompt.id)
    result["all_references_resolved_and_frozen"] = bool(result["resolved"]) and all(
        value["frozen"] for value in result["resolved"].values()
    )
    return result


def _maximum_retrieval_mode(pipeline: PipelineConfiguration) -> RetrievalMode:
    if pipeline.execution_mode is not PipelineExecutionMode.ADAPTIVE:
        return pipeline.retrieval_mode
    allowed = {
        RetrievalMode(value)
        for value in pipeline.adaptive_configuration.get("allowed_retrieval_modes", [])
    }
    for mode in (RetrievalMode.HYBRID, RetrievalMode.DENSE, RetrievalMode.LEXICAL):
        if mode in allowed:
            return mode
    return RetrievalMode.NONE


def _router_maximum_context(
    router: RouterConfiguration | None, *, fallback: int
) -> int:
    if router is None:
        return fallback
    values = [
        int(value)
        for key, value in router.configuration.items()
        if key.endswith("context_budget") and isinstance(value, int | float)
    ]
    return max(values, default=fallback)


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _enum_value(value: object) -> str:
    """Normalize enum-backed ORM values even before a session refresh.

    SQLAlchemy accepts each enum's persisted string during same-session service
    mutations. The object is converted back to its enum class after reload, so
    snapshotting must handle both representations identically.
    """

    member_value = getattr(value, "value", value)
    return str(member_value)
