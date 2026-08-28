from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import (
    ExperimentAttemptStatus,
    ExperimentCellStatus,
    ExperimentStatus,
    RetrievalMode,
)

from .contracts import ExperimentDependencyResolver, ExperimentStore, QueryExecutor
from .schemas import (
    ExperimentCostEstimate,
    ExperimentCreate,
    ExperimentDependencySnapshot,
    ExperimentExecutionReport,
    ExperimentProgress,
    ExperimentRecord,
    ExperimentRunAttempt,
    ExperimentRunCell,
    PipelineCostEstimate,
    PipelineDependencySnapshot,
    QueryExecutionOutcome,
)

Clock = Callable[[], datetime]
PauseCheck = Callable[[], bool]


class ExperimentService:
    """Freeze and execute a reproducible question × pipeline × repetition matrix."""

    def __init__(
        self,
        store: ExperimentStore,
        dependencies: ExperimentDependencyResolver,
        *,
        clock: Clock | None = None,
        experiment_cost_limit: float | None = None,
    ) -> None:
        self.store = store
        self.dependencies = dependencies
        self.clock = clock or (lambda: datetime.now(UTC))
        self.experiment_cost_limit = experiment_cost_limit

    def create(self, payload: ExperimentCreate) -> ExperimentRecord:
        snapshot = self._resolve(payload)
        self._validate_references(payload, snapshot, require_frozen=False)
        experiment = ExperimentRecord(
            id=uuid4(),
            name=payload.name,
            research_question=payload.research_question,
            corpus_version_id=payload.corpus_version_id,
            benchmark_version_id=payload.benchmark_version_id,
            pipeline_configuration_ids=payload.pipeline_configuration_ids,
            repetitions=payload.repetitions,
            code_commit=payload.code_commit,
            stop_on_error=payload.stop_on_error,
            retry_policy=payload.retry_policy,
            analysis_configuration=payload.analysis_configuration,
            dependency_snapshot=snapshot,
            created_at=self.clock(),
        )
        self.store.add_experiment(experiment)
        return experiment

    def freeze(self, experiment_id: UUID) -> ExperimentRecord:
        experiment = self._get(experiment_id)
        if experiment.status is not ExperimentStatus.DRAFT:
            raise DomainError(
                "EXPERIMENT_VERSION_CONFLICT",
                "Only a draft experiment can be frozen.",
            )
        payload = self._creation_payload(experiment)
        snapshot = self._resolve(payload)
        self._validate_references(payload, snapshot, require_frozen=True)
        configuration_hash = _configuration_hash(payload, snapshot)
        frozen = experiment.model_copy(
            update={
                "status": ExperimentStatus.FROZEN,
                "dependency_snapshot": snapshot,
                "configuration_hash": configuration_hash,
                "frozen_at": self.clock(),
            }
        )
        self.store.save_experiment(frozen)
        self.store.add_run_cells(self._build_matrix(frozen))
        return frozen

    def estimate(self, experiment_id: UUID) -> ExperimentCostEstimate:
        experiment = self._get(experiment_id)
        estimate = estimate_maximum_cost(experiment)
        self.store.save_cost_estimate(estimate)
        return estimate

    def start(
        self,
        experiment_id: UUID,
        *,
        execute: QueryExecutor,
        should_pause: PauseCheck | None = None,
    ) -> ExperimentExecutionReport:
        experiment = self._get(experiment_id)
        if experiment.status is not ExperimentStatus.FROZEN:
            raise DomainError(
                "EXPERIMENT_NOT_FROZEN",
                "Only a frozen experiment can start.",
            )
        self._enforce_cost_limit(experiment)
        self._verify_frozen_dependencies(experiment)
        running = experiment.model_copy(
            update={"status": ExperimentStatus.RUNNING, "started_at": self.clock()}
        )
        self.store.save_experiment(running)
        return self._execute(running, execute=execute, should_pause=should_pause)

    def _enforce_cost_limit(self, experiment: ExperimentRecord) -> None:
        if self.experiment_cost_limit is None:
            return
        estimate = estimate_maximum_cost(experiment)
        self.store.save_cost_estimate(estimate)
        if (
            estimate.cost_fully_configured
            and estimate.maximum_expected_cost is not None
            and estimate.maximum_expected_cost > self.experiment_cost_limit
        ):
            raise DomainError(
                "EXPERIMENT_COST_LIMIT",
                "The configured maximum experiment cost exceeds the execution limit.",
            )

    def resume(
        self,
        experiment_id: UUID,
        *,
        execute: QueryExecutor,
        should_pause: PauseCheck | None = None,
    ) -> ExperimentExecutionReport:
        experiment = self._get(experiment_id)
        if experiment.status in {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.COMPLETED_WITH_FAILURES,
        }:
            return self._report(experiment, executed_attempts=0)
        if experiment.status not in {ExperimentStatus.RUNNING, ExperimentStatus.PAUSED}:
            raise DomainError(
                "INVALID_EXPERIMENT_TRANSITION",
                "Only a running or paused experiment can resume.",
            )
        self._verify_frozen_dependencies(experiment)
        self._reconcile_interrupted_cells(experiment)
        running = experiment.model_copy(update={"status": ExperimentStatus.RUNNING})
        self.store.save_experiment(running)
        return self._execute(running, execute=execute, should_pause=should_pause)

    def _execute(
        self,
        experiment: ExperimentRecord,
        *,
        execute: QueryExecutor,
        should_pause: PauseCheck | None,
    ) -> ExperimentExecutionReport:
        executed_attempts = 0
        cells = sorted(self.store.list_run_cells(experiment.id), key=_cell_order)
        for cell in cells:
            if should_pause is not None and should_pause():
                paused = experiment.model_copy(update={"status": ExperimentStatus.PAUSED})
                self.store.save_experiment(paused)
                return self._report(paused, executed_attempts=executed_attempts)
            if cell.status not in {
                ExperimentCellStatus.PLANNED,
                ExperimentCellStatus.RETRYABLE,
            }:
                continue
            if cell.attempt_count >= cell.max_attempts:
                self.store.save_run_cell(
                    cell.model_copy(update={"status": ExperimentCellStatus.FAILED})
                )
                continue

            attempt_number = cell.attempt_count + 1
            started_at = self.clock()
            running_cell = cell.model_copy(
                update={
                    "status": ExperimentCellStatus.RUNNING,
                    "attempt_count": attempt_number,
                    "started_at": cell.started_at or started_at,
                    "finished_at": None,
                }
            )
            self.store.save_run_cell(running_cell)
            attempt = ExperimentRunAttempt(
                experiment_run_id=cell.id,
                attempt_number=attempt_number,
                status=ExperimentAttemptStatus.RUNNING,
                started_at=started_at,
            )
            self.store.add_attempt(attempt)
            executed_attempts += 1

            try:
                outcome = execute(running_cell, running_cell.query_request(experiment))
            except Exception:  # noqa: BLE001 - converted to a sanitized runner failure
                outcome = QueryExecutionOutcome(
                    succeeded=False,
                    failure_code="EXPERIMENT_RUNNER_FAILURE",
                    failure_message="The query executor raised an exception.",
                )
            self._record_outcome(experiment, running_cell, attempt, outcome)

            if not outcome.succeeded and experiment.stop_on_error:
                paused = experiment.model_copy(update={"status": ExperimentStatus.PAUSED})
                self.store.save_experiment(paused)
                return self._report(paused, executed_attempts=executed_attempts)

        finalized = self._finalize(experiment)
        return self._report(finalized, executed_attempts=executed_attempts)

    def _record_outcome(
        self,
        experiment: ExperimentRecord,
        cell: ExperimentRunCell,
        attempt: ExperimentRunAttempt,
        outcome: QueryExecutionOutcome,
    ) -> None:
        finished_at = self.clock()
        if outcome.succeeded:
            self.store.save_attempt(
                attempt.model_copy(
                    update={
                        "status": ExperimentAttemptStatus.SUCCEEDED,
                        "query_run_id": outcome.query_run_id,
                        "finished_at": finished_at,
                    }
                )
            )
            self.store.save_run_cell(
                cell.model_copy(
                    update={
                        "status": ExperimentCellStatus.SUCCEEDED,
                        "query_run_id": outcome.query_run_id,
                        "last_failure_code": None,
                        "last_failure_message": None,
                        "finished_at": finished_at,
                    }
                )
            )
            return

        failure_code = outcome.failure_code or "EXPERIMENT_RUNNER_FAILURE"
        retryable = (
            failure_code in experiment.retry_policy.retryable_failure_codes
            and cell.attempt_count < cell.max_attempts
        )
        status = (
            ExperimentCellStatus.RETRYABLE
            if retryable
            else ExperimentCellStatus.FAILED
        )
        self.store.save_attempt(
            attempt.model_copy(
                update={
                    "status": ExperimentAttemptStatus.FAILED,
                    "query_run_id": outcome.query_run_id,
                    "failure_code": failure_code,
                    "failure_message": outcome.failure_message,
                    "finished_at": finished_at,
                }
            )
        )
        self.store.save_run_cell(
            cell.model_copy(
                update={
                    "status": status,
                    "query_run_id": outcome.query_run_id,
                    "last_failure_code": failure_code,
                    "last_failure_message": outcome.failure_message,
                    "finished_at": finished_at,
                }
            )
        )

    def _reconcile_interrupted_cells(self, experiment: ExperimentRecord) -> None:
        for cell in self.store.list_run_cells(experiment.id):
            if cell.status is not ExperimentCellStatus.RUNNING:
                continue
            outcome = self.store.find_attempt_outcome(cell.id, cell.attempt_count)
            attempt = self.store.get_attempt(cell.id, cell.attempt_count)
            if outcome is not None and attempt is not None:
                self._record_outcome(experiment, cell, attempt, outcome)
                continue
            now = self.clock()
            if attempt is not None:
                self.store.save_attempt(
                    attempt.model_copy(
                        update={
                            "status": ExperimentAttemptStatus.INTERRUPTED,
                            "failure_code": "EXPERIMENT_INTERRUPTED",
                            "failure_message": (
                                "Execution was interrupted before an outcome was linked."
                            ),
                            "finished_at": now,
                        }
                    )
                )
            retryable = cell.attempt_count < cell.max_attempts
            self.store.save_run_cell(
                cell.model_copy(
                    update={
                        "status": (
                            ExperimentCellStatus.RETRYABLE
                            if retryable
                            else ExperimentCellStatus.FAILED
                        ),
                        "last_failure_code": "EXPERIMENT_INTERRUPTED",
                        "last_failure_message": "Execution was interrupted before completion.",
                        "finished_at": now,
                    }
                )
            )

    def _finalize(self, experiment: ExperimentRecord) -> ExperimentRecord:
        progress = _progress(self.store.list_run_cells(experiment.id))
        if progress.planned or progress.running or progress.retryable:
            status = ExperimentStatus.PAUSED
            completed_at = None
        elif progress.failed:
            status = ExperimentStatus.COMPLETED_WITH_FAILURES
            completed_at = self.clock()
        else:
            status = ExperimentStatus.COMPLETED
            completed_at = self.clock()
        finalized = experiment.model_copy(
            update={"status": status, "completed_at": completed_at}
        )
        self.store.save_experiment(finalized)
        return finalized

    def _verify_frozen_dependencies(self, experiment: ExperimentRecord) -> None:
        current = self.dependencies.resolve(
            corpus_version_id=experiment.corpus_version_id,
            benchmark_version_id=experiment.benchmark_version_id,
            pipeline_configuration_ids=experiment.pipeline_configuration_ids,
        )
        if _snapshot_hash(current) != _snapshot_hash(experiment.dependency_snapshot):
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_CHANGED",
                "A frozen experiment dependency no longer matches its recorded snapshot.",
            )

    def _build_matrix(self, experiment: ExperimentRecord) -> tuple[ExperimentRunCell, ...]:
        cells: list[ExperimentRunCell] = []
        questions = sorted(
            experiment.dependency_snapshot.questions, key=lambda value: str(value.id)
        )
        for question in questions:
            for pipeline_id in experiment.pipeline_configuration_ids:
                for repetition_index in range(1, experiment.repetitions + 1):
                    key = (
                        f"experiment:{experiment.id}:question:{question.id}:"
                        f"pipeline:{pipeline_id}:repetition:{repetition_index}"
                    )
                    idempotency_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
                    random_seed = int(idempotency_key[:8], 16) & 0x7FFFFFFF
                    cells.append(
                        ExperimentRunCell(
                            id=uuid5(NAMESPACE_URL, key),
                            experiment_id=experiment.id,
                            benchmark_question_id=question.id,
                            pipeline_configuration_id=pipeline_id,
                            repetition_index=repetition_index,
                            query_text=question.question_text,
                            idempotency_key=idempotency_key,
                            random_seed=random_seed,
                            max_attempts=experiment.retry_policy.max_attempts,
                        )
                    )
        return tuple(cells)

    def _resolve(self, payload: ExperimentCreate) -> ExperimentDependencySnapshot:
        return self.dependencies.resolve(
            corpus_version_id=payload.corpus_version_id,
            benchmark_version_id=payload.benchmark_version_id,
            pipeline_configuration_ids=payload.pipeline_configuration_ids,
        )

    @staticmethod
    def _validate_references(
        payload: ExperimentCreate,
        snapshot: ExperimentDependencySnapshot,
        *,
        require_frozen: bool,
    ) -> None:
        if snapshot.corpus_version_id != payload.corpus_version_id:
            raise DomainError("INVALID_EXPERIMENT_CONFIGURATION", "Corpus snapshot mismatch.")
        if snapshot.benchmark_version_id != payload.benchmark_version_id:
            raise DomainError("INVALID_EXPERIMENT_CONFIGURATION", "Benchmark snapshot mismatch.")
        if snapshot.benchmark_corpus_version_id != payload.corpus_version_id:
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "The benchmark and experiment must use the same corpus version.",
            )
        if tuple(value.id for value in snapshot.pipelines) != payload.pipeline_configuration_ids:
            raise DomainError("INVALID_EXPERIMENT_CONFIGURATION", "Pipeline snapshot mismatch.")
        analysis = payload.analysis_configuration
        pipeline_by_id = {value.id: value for value in snapshot.pipelines}
        referenced_ids = {
            value
            for value in (
                analysis.adaptive_pipeline_id,
                analysis.fixed_reference_pipeline_id,
                *analysis.best_observed_fixed_pipeline_ids,
            )
            if value is not None
        }
        if not referenced_ids.issubset(pipeline_by_id):
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "Analysis pipeline references must belong to the experiment.",
            )
        if analysis.adaptive_pipeline_id is not None and (
            pipeline_by_id[analysis.adaptive_pipeline_id].execution_mode.value != "adaptive"
        ):
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "The adaptive analysis pipeline must use adaptive execution mode.",
            )
        fixed_ids = {
            value
            for value in (
                analysis.fixed_reference_pipeline_id,
                *analysis.best_observed_fixed_pipeline_ids,
            )
            if value is not None
        }
        if any(pipeline_by_id[value].execution_mode.value != "fixed" for value in fixed_ids):
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "Reference and best-observed analysis pipelines must be fixed pipelines.",
            )
        if not snapshot.questions:
            raise DomainError(
                "INVALID_EXPERIMENT_CONFIGURATION",
                "An experiment benchmark must contain at least one question.",
            )
        if not require_frozen:
            return
        if snapshot.corpus_status != "ready" or not snapshot.corpus_frozen:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_NOT_FROZEN",
                "The corpus version must be frozen and ready before experiment freeze.",
            )
        if snapshot.benchmark_status != "frozen" or not snapshot.benchmark_frozen:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_NOT_FROZEN",
                "The benchmark version must be frozen before experiment freeze.",
            )
        if any(value.annotation_status != "reviewed" for value in snapshot.questions):
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_NOT_FROZEN",
                "Every benchmark question must be human-reviewed before experiment freeze.",
            )
        unavailable = [value.name for value in snapshot.pipelines if not value.frozen]
        unavailable.extend(
            value.name
            for value in snapshot.pipelines
            if value.adaptive_router_frozen is False
        )
        if unavailable:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_NOT_FROZEN",
                "Pipelines and adaptive router configurations must be frozen: "
                + ", ".join(sorted(set(unavailable))),
            )
        unavailable_capabilities = sorted(
            {
                capability
                for pipeline in snapshot.pipelines
                for capability in pipeline.unavailable_capabilities
            }
        )
        if unavailable_capabilities:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_UNAVAILABLE",
                "Required providers are unavailable: "
                + ", ".join(unavailable_capabilities),
            )
        invalid_prompts = [
            pipeline.name
            for pipeline in snapshot.pipelines
            if pipeline.prompt_snapshot.get("all_references_resolved_and_frozen") is False
        ]
        if invalid_prompts:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_NOT_FROZEN",
                "Every pipeline must resolve a frozen prompt snapshot: "
                + ", ".join(sorted(invalid_prompts)),
            )
        invalid_indexes = [
            pipeline.name
            for pipeline in snapshot.pipelines
            if pipeline.configuration_snapshot and not _required_indexes_ready(pipeline)
        ]
        if invalid_indexes:
            raise DomainError(
                "EXPERIMENT_DEPENDENCY_UNAVAILABLE",
                "Required retrieval indexes are not ready: "
                + ", ".join(sorted(invalid_indexes)),
            )

    @staticmethod
    def _creation_payload(experiment: ExperimentRecord) -> ExperimentCreate:
        return ExperimentCreate(
            name=experiment.name,
            research_question=experiment.research_question,
            corpus_version_id=experiment.corpus_version_id,
            benchmark_version_id=experiment.benchmark_version_id,
            pipeline_configuration_ids=experiment.pipeline_configuration_ids,
            repetitions=experiment.repetitions,
            code_commit=experiment.code_commit,
            stop_on_error=experiment.stop_on_error,
            retry_policy=experiment.retry_policy,
            analysis_configuration=experiment.analysis_configuration,
        )

    def _get(self, experiment_id: UUID) -> ExperimentRecord:
        experiment = self.store.get_experiment(experiment_id)
        if experiment is None:
            raise DomainError("EXPERIMENT_NOT_FOUND", "Experiment not found.", status_code=404)
        return experiment

    def _report(
        self, experiment: ExperimentRecord, *, executed_attempts: int
    ) -> ExperimentExecutionReport:
        return ExperimentExecutionReport(
            experiment=experiment,
            progress=_progress(self.store.list_run_cells(experiment.id)),
            executed_attempts=executed_attempts,
        )


def estimate_maximum_cost(experiment: ExperimentRecord) -> ExperimentCostEstimate:
    questions = experiment.dependency_snapshot.questions
    query_tokens = sum(_estimate_tokens(value.question_text) for value in questions)
    question_count = len(questions)
    estimates = tuple(
        _estimate_pipeline_cost(
            pipeline,
            question_count=question_count,
            query_tokens=query_tokens,
            repetitions=experiment.repetitions,
        )
        for pipeline in experiment.dependency_snapshot.pipelines
    )
    currencies = {value.currency for value in estimates}
    fully_configured = all(value.maximum_expected_cost is not None for value in estimates)
    currency: str | None
    maximum_cost: float | None
    if fully_configured and len(currencies) == 1:
        maximum_cost = sum(value.maximum_expected_cost or 0 for value in estimates)
        currency = next(iter(currencies))
    else:
        maximum_cost = None
        currency = next(iter(currencies)) if len(currencies) == 1 else None
    return ExperimentCostEstimate(
        experiment_id=experiment.id,
        run_count=question_count * len(estimates) * experiment.repetitions,
        per_pipeline=estimates,
        maximum_expected_cost=maximum_cost,
        currency=currency,
        cost_fully_configured=fully_configured and len(currencies) == 1,
    )


def _estimate_pipeline_cost(
    pipeline: PipelineDependencySnapshot,
    *,
    question_count: int,
    query_tokens: int,
    repetitions: int,
) -> PipelineCostEstimate:
    runs = question_count * repetitions
    generation_input = repetitions * (
        query_tokens
        + question_count * (pipeline.maximum_context_tokens + pipeline.prompt_overhead_tokens)
    )
    generation_output = runs * pipeline.maximum_output_tokens
    uses_embedding = pipeline.retrieval_mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}
    embedding_input = repetitions * query_tokens if uses_embedding else 0
    reranker_calls = runs if pipeline.reranking_enabled else 0
    missing: list[str] = []

    pricing = pipeline.pricing
    if pricing.generation_billable:
        if pricing.generation_input_per_million_tokens is None:
            missing.append("generation_input_per_million_tokens")
        if pricing.generation_output_per_million_tokens is None:
            missing.append("generation_output_per_million_tokens")
        generation_cost = (
            None
            if missing
            else (
                generation_input * (pricing.generation_input_per_million_tokens or 0)
                + generation_output * (pricing.generation_output_per_million_tokens or 0)
            )
            / 1_000_000
        )
    else:
        generation_cost = 0.0

    if uses_embedding and pricing.embedding_billable:
        if pricing.embedding_input_per_million_tokens is None:
            missing.append("embedding_input_per_million_tokens")
            embedding_cost = None
        else:
            embedding_cost = (
                embedding_input * pricing.embedding_input_per_million_tokens / 1_000_000
            )
    else:
        embedding_cost = 0.0

    if pipeline.reranking_enabled and pricing.reranker_billable:
        if pricing.reranker_per_call is None:
            missing.append("reranker_per_call")
            reranker_cost = None
        else:
            reranker_cost = reranker_calls * pricing.reranker_per_call
    else:
        reranker_cost = 0.0

    components = (generation_cost, embedding_cost, reranker_cost)
    maximum_cost = (
        None
        if any(value is None for value in components)
        else sum(value for value in components if value is not None)
    )
    return PipelineCostEstimate(
        pipeline_configuration_id=pipeline.id,
        run_count=runs,
        maximum_generation_input_tokens=generation_input,
        maximum_generation_output_tokens=generation_output,
        maximum_embedding_input_tokens=embedding_input,
        maximum_reranker_calls=reranker_calls,
        generation_cost=generation_cost,
        embedding_cost=embedding_cost,
        reranker_cost=reranker_cost,
        maximum_expected_cost=maximum_cost,
        currency=pricing.currency,
        missing_pricing=tuple(missing),
    )


def _configuration_hash(
    payload: ExperimentCreate, snapshot: ExperimentDependencySnapshot
) -> str:
    value = {
        "schema_version": "experiment-configuration-v1",
        "experiment": payload.model_dump(mode="json"),
        "dependencies": snapshot.model_dump(mode="json"),
    }
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _snapshot_hash(snapshot: ExperimentDependencySnapshot) -> str:
    return hashlib.sha256(canonical_json(snapshot.model_dump(mode="json"))).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _required_indexes_ready(pipeline: PipelineDependencySnapshot) -> bool:
    required: set[str]
    if pipeline.retrieval_mode is RetrievalMode.NONE:
        return True
    if pipeline.retrieval_mode is RetrievalMode.LEXICAL:
        required = {"lexical"}
    elif pipeline.retrieval_mode is RetrievalMode.DENSE:
        required = {"dense"}
    else:
        required = {"lexical", "dense"}
    ready = {
        str(index.get("index_type"))
        for index in pipeline.index_snapshots
        if index.get("status") == "ready"
    }
    return required <= ready


def _cell_order(cell: ExperimentRunCell) -> tuple[str, str, int]:
    return (
        str(cell.benchmark_question_id),
        str(cell.pipeline_configuration_id),
        cell.repetition_index,
    )


def _progress(cells: Sequence[ExperimentRunCell]) -> ExperimentProgress:
    counts = {status: 0 for status in ExperimentCellStatus}
    for cell in cells:
        counts[cell.status] += 1
    return ExperimentProgress(
        total=len(cells),
        planned=counts[ExperimentCellStatus.PLANNED],
        running=counts[ExperimentCellStatus.RUNNING],
        retryable=counts[ExperimentCellStatus.RETRYABLE],
        succeeded=counts[ExperimentCellStatus.SUCCEEDED],
        failed=counts[ExperimentCellStatus.FAILED],
    )
