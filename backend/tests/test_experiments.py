from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Answerability,
    Benchmark,
    BenchmarkAnnotationStatus,
    BenchmarkDifficulty,
    BenchmarkQuestion,
    BenchmarkQuestionType,
    BenchmarkVersion,
    BenchmarkVersionStatus,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    PipelineConfiguration,
    PipelineExecutionMode,
    RetrievalMode,
)
from backend.app.experiments import (
    ExperimentCellStatus,
    ExperimentCostEstimate,
    ExperimentCreate,
    ExperimentDependencySnapshot,
    ExperimentRecord,
    ExperimentRunAttempt,
    ExperimentRunCell,
    ExperimentService,
    ExperimentStatus,
    PipelineDependencySnapshot,
    PricingSnapshot,
    QueryExecutionOutcome,
    QuestionSnapshot,
    RetryPolicy,
    SQLAlchemyExperimentDependencyResolver,
    SQLAlchemyExperimentStore,
)
from backend.app.query_runtime.schemas import QueryRunCreate
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@dataclass
class MemoryStore:
    experiments: dict[UUID, ExperimentRecord] = field(default_factory=dict)
    cells: dict[UUID, ExperimentRunCell] = field(default_factory=dict)
    attempts: dict[tuple[UUID, int], ExperimentRunAttempt] = field(default_factory=dict)
    outcomes: dict[tuple[UUID, int], QueryExecutionOutcome] = field(default_factory=dict)
    cost_estimates: dict[UUID, ExperimentCostEstimate] = field(default_factory=dict)

    def add_experiment(self, experiment: ExperimentRecord) -> None:
        if experiment.id in self.experiments:
            raise AssertionError("duplicate experiment")
        self.experiments[experiment.id] = experiment

    def get_experiment(self, experiment_id: UUID) -> ExperimentRecord | None:
        return self.experiments.get(experiment_id)

    def save_experiment(self, experiment: ExperimentRecord) -> None:
        self.experiments[experiment.id] = experiment

    def save_cost_estimate(self, estimate: ExperimentCostEstimate) -> None:
        self.cost_estimates[estimate.experiment_id] = estimate

    def add_run_cells(self, cells: tuple[ExperimentRunCell, ...]) -> None:
        for cell in cells:
            if cell.id in self.cells or any(
                value.idempotency_key == cell.idempotency_key for value in self.cells.values()
            ):
                raise AssertionError("duplicate matrix cell")
            self.cells[cell.id] = cell

    def list_run_cells(self, experiment_id: UUID) -> list[ExperimentRunCell]:
        return [
            value for value in self.cells.values() if value.experiment_id == experiment_id
        ]

    def save_run_cell(self, cell: ExperimentRunCell) -> None:
        self.cells[cell.id] = cell

    def add_attempt(self, attempt: ExperimentRunAttempt) -> None:
        key = (attempt.experiment_run_id, attempt.attempt_number)
        if key in self.attempts:
            raise AssertionError("duplicate attempt")
        self.attempts[key] = attempt

    def save_attempt(self, attempt: ExperimentRunAttempt) -> None:
        self.attempts[(attempt.experiment_run_id, attempt.attempt_number)] = attempt

    def get_attempt(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> ExperimentRunAttempt | None:
        return self.attempts.get((experiment_run_id, attempt_number))

    def find_attempt_outcome(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> QueryExecutionOutcome | None:
        return self.outcomes.get((experiment_run_id, attempt_number))


@dataclass
class MutableResolver:
    snapshot: ExperimentDependencySnapshot

    def resolve(
        self,
        *,
        corpus_version_id: UUID,
        benchmark_version_id: UUID,
        pipeline_configuration_ids: tuple[UUID, ...],
    ) -> ExperimentDependencySnapshot:
        del corpus_version_id, benchmark_version_id, pipeline_configuration_ids
        return self.snapshot


def _pipeline(
    pipeline_id: UUID,
    *,
    name: str,
    mode: RetrievalMode = RetrievalMode.HYBRID,
    frozen: bool = True,
    pricing: PricingSnapshot | None = None,
) -> PipelineDependencySnapshot:
    return PipelineDependencySnapshot(
        id=pipeline_id,
        name=name,
        version=1,
        frozen=frozen,
        configuration_hash=("a" if name == "first" else "c") * 64,
        prompt_snapshot={"grounded_generation": {"prompt_id": "grounded", "version": 1}},
        generation_provider="fake",
        generation_model="fake-v1",
        retrieval_mode=mode,
        reranking_enabled=mode is RetrievalMode.HYBRID,
        maximum_context_tokens=100,
        maximum_output_tokens=20,
        prompt_overhead_tokens=10,
        pricing=pricing
        or PricingSnapshot(
            generation_billable=False,
            embedding_billable=False,
            reranker_billable=False,
        ),
    )


def _snapshot(
    *,
    pipeline_ids: tuple[UUID, ...] | None = None,
    corpus_status: str = "ready",
    corpus_frozen: bool = True,
    benchmark_status: str = "frozen",
    benchmark_frozen: bool = True,
    pipelines: tuple[PipelineDependencySnapshot, ...] | None = None,
) -> ExperimentDependencySnapshot:
    corpus_id = UUID("00000000-0000-0000-0000-000000000001")
    benchmark_id = UUID("00000000-0000-0000-0000-000000000002")
    selected_ids = pipeline_ids or (
        UUID("00000000-0000-0000-0000-000000000011"),
        UUID("00000000-0000-0000-0000-000000000012"),
    )
    selected_pipelines = pipelines or tuple(
        _pipeline(value, name="first" if index == 0 else "second")
        for index, value in enumerate(selected_ids)
    )
    return ExperimentDependencySnapshot(
        corpus_version_id=corpus_id,
        corpus_status=corpus_status,
        corpus_frozen=corpus_frozen,
        corpus_content_hash="d" * 64,
        benchmark_version_id=benchmark_id,
        benchmark_corpus_version_id=corpus_id,
        benchmark_status=benchmark_status,
        benchmark_frozen=benchmark_frozen,
        benchmark_snapshot_hash="e" * 64,
        questions=(
            QuestionSnapshot(
                id=UUID("00000000-0000-0000-0000-000000000021"),
                question_text="abcd",
                annotation_status="reviewed",
            ),
            QuestionSnapshot(
                id=UUID("00000000-0000-0000-0000-000000000022"),
                question_text="abcdefgh",
                annotation_status="reviewed",
            ),
        ),
        pipelines=selected_pipelines,
    )


def _payload(snapshot: ExperimentDependencySnapshot, *, repetitions: int = 1) -> ExperimentCreate:
    return ExperimentCreate(
        name="pilot-v1",
        research_question="Do retrieval strategies differ?",
        corpus_version_id=snapshot.corpus_version_id,
        benchmark_version_id=snapshot.benchmark_version_id,
        pipeline_configuration_ids=tuple(value.id for value in snapshot.pipelines),
        repetitions=repetitions,
        code_commit="abcdef1234567890",
    )


def _frozen_service(
    *,
    repetitions: int = 1,
    retry_policy: RetryPolicy | None = None,
    snapshot: ExperimentDependencySnapshot | None = None,
) -> tuple[ExperimentService, MemoryStore, MutableResolver, ExperimentRecord]:
    value = snapshot or _snapshot()
    store = MemoryStore()
    resolver = MutableResolver(value)
    service = ExperimentService(
        store,
        resolver,
        clock=lambda: datetime(2026, 8, 26, tzinfo=UTC),
    )
    payload = _payload(value, repetitions=repetitions)
    if retry_policy is not None:
        payload = payload.model_copy(update={"retry_policy": retry_policy})
    draft = service.create(payload)
    return service, store, resolver, service.freeze(draft.id)


def test_freeze_requires_frozen_dependencies_and_draft_remains_editable() -> None:
    snapshot = _snapshot(corpus_status="draft", corpus_frozen=False)
    store = MemoryStore()
    resolver = MutableResolver(snapshot)
    service = ExperimentService(store, resolver)
    draft = service.create(_payload(snapshot))

    assert draft.status is ExperimentStatus.DRAFT
    with pytest.raises(DomainError) as raised:
        service.freeze(draft.id)
    assert raised.value.code == "EXPERIMENT_DEPENDENCY_NOT_FROZEN"


def test_freeze_builds_complete_deterministic_run_matrix_once() -> None:
    service, store, _, frozen = _frozen_service(repetitions=2)
    cells = sorted(store.list_run_cells(frozen.id), key=lambda value: value.idempotency_key)

    assert frozen.status is ExperimentStatus.FROZEN
    assert frozen.configuration_hash and len(frozen.configuration_hash) == 64
    assert len(cells) == 2 * 2 * 2
    assert {value.repetition_index for value in cells} == {1, 2}
    assert len({value.id for value in cells}) == len(cells)
    assert len({value.idempotency_key for value in cells}) == len(cells)
    with pytest.raises(DomainError) as raised:
        service.freeze(frozen.id)
    assert raised.value.code == "EXPERIMENT_VERSION_CONFLICT"


def test_complete_start_and_resume_do_not_duplicate_valid_runs() -> None:
    service, store, _, frozen = _frozen_service()
    calls: list[UUID] = []

    def execute(
        _cell: ExperimentRunCell, _request: QueryRunCreate
    ) -> QueryExecutionOutcome:
        query_run_id = uuid4()
        calls.append(query_run_id)
        return QueryExecutionOutcome(query_run_id=query_run_id, succeeded=True)

    report = service.start(frozen.id, execute=execute)
    resumed = service.resume(frozen.id, execute=execute)

    assert report.experiment.status is ExperimentStatus.COMPLETED
    assert report.progress.succeeded == 4
    assert len(calls) == 4
    assert resumed.executed_attempts == 0
    assert len(calls) == 4


def test_resume_retries_only_infrastructure_failures_under_policy() -> None:
    service, store, _, frozen = _frozen_service(
        snapshot=_snapshot(pipeline_ids=(UUID("00000000-0000-0000-0000-000000000011"),))
    )
    first_question = frozen.dependency_snapshot.questions[0].id
    failed_once = False

    def execute(
        _cell: ExperimentRunCell, request: QueryRunCreate
    ) -> QueryExecutionOutcome:
        nonlocal failed_once
        question_id = request.benchmark_question_id
        if question_id == first_question and not failed_once:
            failed_once = True
            return QueryExecutionOutcome(
                query_run_id=uuid4(),
                succeeded=False,
                failure_code="TIMEOUT",
                failure_message="sanitized timeout",
            )
        return QueryExecutionOutcome(query_run_id=uuid4(), succeeded=True)

    started = service.start(frozen.id, execute=execute)
    resumed = service.resume(frozen.id, execute=execute)
    cells = store.list_run_cells(frozen.id)

    assert started.experiment.status is ExperimentStatus.PAUSED
    assert started.progress.retryable == 1
    assert resumed.experiment.status is ExperimentStatus.COMPLETED
    assert all(value.status is ExperimentCellStatus.SUCCEEDED for value in cells)
    assert sorted(value.attempt_count for value in cells) == [1, 2]


@pytest.mark.parametrize("failure_code", ["INVALID_QUERY", "INVALID_STRUCTURED_OUTPUT"])
def test_non_infrastructure_failure_is_not_retried(failure_code: str) -> None:
    service, store, _, frozen = _frozen_service(
        snapshot=_snapshot(pipeline_ids=(UUID("00000000-0000-0000-0000-000000000011"),))
    )

    report = service.start(
        frozen.id,
        execute=lambda _cell, _request: QueryExecutionOutcome(
            query_run_id=uuid4(),
            succeeded=False,
            failure_code=failure_code,
        ),
    )

    assert report.experiment.status is ExperimentStatus.COMPLETED_WITH_FAILURES
    assert report.progress.failed == 2
    assert all(value.attempt_count == 1 for value in store.list_run_cells(frozen.id))


def test_interrupted_run_resumes_without_reexecuting_prior_success() -> None:
    service, store, _, frozen = _frozen_service(
        snapshot=_snapshot(pipeline_ids=(UUID("00000000-0000-0000-0000-000000000011"),))
    )
    calls = 0

    def interrupt_second(
        _cell: ExperimentRunCell, _request: QueryRunCreate
    ) -> QueryExecutionOutcome:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return QueryExecutionOutcome(query_run_id=uuid4(), succeeded=True)

    with pytest.raises(KeyboardInterrupt):
        service.start(frozen.id, execute=interrupt_second)

    cells_after_interrupt = store.list_run_cells(frozen.id)
    assert sum(
        value.status is ExperimentCellStatus.SUCCEEDED
        for value in cells_after_interrupt
    ) == 1
    assert sum(
        value.status is ExperimentCellStatus.RUNNING
        for value in cells_after_interrupt
    ) == 1

    resumed = service.resume(
        frozen.id,
        execute=lambda _cell, _request: QueryExecutionOutcome(
            query_run_id=uuid4(), succeeded=True
        ),
    )
    assert resumed.experiment.status is ExperimentStatus.COMPLETED
    assert resumed.progress.succeeded == 2
    assert sorted(value.attempt_count for value in store.list_run_cells(frozen.id)) == [1, 2]


def test_dependency_drift_blocks_start_of_frozen_experiment() -> None:
    service, _, resolver, frozen = _frozen_service()
    resolver.snapshot = resolver.snapshot.model_copy(
        update={"benchmark_snapshot_hash": "f" * 64}
    )

    with pytest.raises(DomainError) as raised:
        service.start(
            frozen.id,
            execute=lambda _cell, _request: QueryExecutionOutcome(
                query_run_id=uuid4(), succeeded=True
            ),
        )
    assert raised.value.code == "EXPERIMENT_DEPENDENCY_CHANGED"


def test_cost_estimate_is_hand_calculated_and_missing_pricing_stays_missing() -> None:
    configured_id = UUID("00000000-0000-0000-0000-000000000011")
    unknown_id = UUID("00000000-0000-0000-0000-000000000012")
    configured = _pipeline(
        configured_id,
        name="first",
        pricing=PricingSnapshot(
            generation_input_per_million_tokens=1.0,
            generation_output_per_million_tokens=2.0,
            embedding_input_per_million_tokens=3.0,
            reranker_billable=True,
            reranker_per_call=0.01,
        ),
    )
    unknown = _pipeline(
        unknown_id,
        name="second",
        mode=RetrievalMode.NONE,
        pricing=PricingSnapshot(
            generation_input_per_million_tokens=None,
            generation_output_per_million_tokens=2.0,
            embedding_billable=False,
        ),
    )
    service, _, _, frozen = _frozen_service(
        repetitions=2,
        snapshot=_snapshot(pipelines=(configured, unknown)),
    )

    estimate = service.estimate(frozen.id)
    first, second = estimate.per_pipeline

    assert estimate.run_count == 8
    assert first.maximum_generation_input_tokens == 446
    assert first.maximum_generation_output_tokens == 80
    assert first.maximum_embedding_input_tokens == 6
    assert first.maximum_reranker_calls == 4
    assert first.generation_cost == pytest.approx(0.000606)
    assert first.embedding_cost == pytest.approx(0.000018)
    assert first.reranker_cost == pytest.approx(0.04)
    assert first.maximum_expected_cost == pytest.approx(0.040624)
    assert second.maximum_expected_cost is None
    assert second.missing_pricing == ("generation_input_per_million_tokens",)
    assert estimate.maximum_expected_cost is None
    assert estimate.cost_fully_configured is False


def test_complete_cost_above_limit_blocks_start_but_unknown_cost_does_not() -> None:
    pipeline_id = UUID("00000000-0000-0000-0000-000000000011")
    configured = _pipeline(
        pipeline_id,
        name="first",
        pricing=PricingSnapshot(
            generation_input_per_million_tokens=1.0,
            generation_output_per_million_tokens=2.0,
            embedding_input_per_million_tokens=3.0,
            reranker_billable=True,
            reranker_per_call=1.0,
        ),
    )
    service, store, resolver, frozen = _frozen_service(
        snapshot=_snapshot(pipelines=(configured,))
    )
    del service
    limited = ExperimentService(store, resolver, experiment_cost_limit=0.01)
    with pytest.raises(DomainError) as raised:
        limited.start(
            frozen.id,
            execute=lambda _cell, _request: QueryExecutionOutcome(
                query_run_id=uuid4(), succeeded=True
            ),
        )
    assert raised.value.code == "EXPERIMENT_COST_LIMIT"

    unknown = configured.model_copy(
        update={
            "pricing": PricingSnapshot(
                generation_input_per_million_tokens=None,
                generation_output_per_million_tokens=None,
                embedding_input_per_million_tokens=None,
                reranker_billable=True,
                reranker_per_call=None,
            )
        }
    )
    unknown_service, unknown_store, unknown_resolver, unknown_frozen = _frozen_service(
        snapshot=_snapshot(pipelines=(unknown,))
    )
    del unknown_service
    unknown_limited = ExperimentService(
        unknown_store, unknown_resolver, experiment_cost_limit=0.0
    )
    report = unknown_limited.start(
        unknown_frozen.id,
        execute=lambda _cell, _request: QueryExecutionOutcome(
            query_run_id=uuid4(), succeeded=True
        ),
    )
    assert report.experiment.status is ExperimentStatus.COMPLETED


def _persist_orm_dependencies(
    session: Session,
) -> tuple[CorpusVersion, BenchmarkVersion, PipelineConfiguration]:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    corpus = Corpus(name="Experiment corpus")
    session.add(corpus)
    session.flush()
    corpus_version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="v1",
        status=CorpusVersionStatus.READY,
        content_hash="1" * 64,
        frozen_at=now,
    )
    session.add(corpus_version)
    session.flush()
    benchmark = Benchmark(name="Experiment benchmark")
    session.add(benchmark)
    session.flush()
    benchmark_version = BenchmarkVersion(
        benchmark_id=benchmark.id,
        corpus_version_id=corpus_version.id,
        version=1,
        status=BenchmarkVersionStatus.DRAFT,
    )
    session.add(benchmark_version)
    session.flush()
    question = BenchmarkQuestion(
        benchmark_version_id=benchmark_version.id,
        question_text="What is reported?",
        question_type=BenchmarkQuestionType.DIRECT_FACT_LOOKUP,
        difficulty=BenchmarkDifficulty.EASY,
        answerable=True,
        expected_answerability=Answerability.ANSWERABLE,
        reference_answer="A reported value.",
        annotation_status=BenchmarkAnnotationStatus.REVIEWED,
    )
    session.add(question)
    pipeline = PipelineConfiguration(
        name="no-rag-v1",
        version=1,
        execution_mode=PipelineExecutionMode.FIXED,
        retrieval_mode=RetrievalMode.NONE,
        lexical_configuration={},
        dense_configuration={},
        fusion_configuration={},
        reranker_configuration={"enabled": False, "provider": "fake"},
        query_processing_configuration={},
        context_configuration={"token_budget": 100},
        generation_configuration={
            "provider": "fake",
            "model": "fake-v1",
            "max_output_tokens": 20,
        },
        citation_configuration={},
        prompt_versions={},
        configuration_hash="2" * 64,
        frozen_at=now,
    )
    session.add(pipeline)
    session.commit()
    benchmark_version.status = BenchmarkVersionStatus.FROZEN
    benchmark_version.frozen_at = now
    session.commit()
    return corpus_version, benchmark_version, pipeline


def test_sqlalchemy_store_and_resolver_persist_frozen_matrix_and_estimate(
    session: Session,
) -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    corpus_version, benchmark_version, pipeline = _persist_orm_dependencies(session)

    resolver = SQLAlchemyExperimentDependencyResolver(session)
    store = SQLAlchemyExperimentStore(session)
    service = ExperimentService(store, resolver, clock=lambda: now)
    payload = ExperimentCreate(
        name="orm-pilot",
        research_question="Does the runner persist?",
        corpus_version_id=corpus_version.id,
        benchmark_version_id=benchmark_version.id,
        pipeline_configuration_ids=(pipeline.id,),
        repetitions=1,
        code_commit="abcdef123456",
    )

    draft = service.create(payload)
    frozen = service.freeze(draft.id)
    estimate = service.estimate(frozen.id)
    reloaded = store.get_experiment(frozen.id)
    cells = store.list_run_cells(frozen.id)

    assert reloaded is not None
    assert reloaded.status is ExperimentStatus.FROZEN
    assert reloaded.dependency_snapshot.benchmark_snapshot_hash
    assert len(cells) == 1
    assert len(cells[0].idempotency_key) == 64
    assert estimate.maximum_expected_cost == 0.0
    assert estimate.cost_fully_configured is True


def test_experiment_api_create_freeze_estimate_and_paginated_reads(
    session: Session, client: TestClient
) -> None:
    corpus_version, benchmark_version, pipeline = _persist_orm_dependencies(session)
    response = client.post(
        "/api/v1/experiments",
        json={
            "name": "api-pilot",
            "research_question": "Does the API preserve the frozen matrix?",
            "corpus_version_id": str(corpus_version.id),
            "benchmark_version_id": str(benchmark_version.id),
            "pipeline_configuration_ids": [str(pipeline.id)],
            "repetitions": 1,
            "code_commit": "abcdef123456",
        },
    )
    assert response.status_code == 201
    experiment_id = response.json()["id"]

    frozen = client.post(f"/api/v1/experiments/{experiment_id}/freeze")
    estimate = client.post(f"/api/v1/experiments/{experiment_id}/estimate")
    detail = client.get(f"/api/v1/experiments/{experiment_id}")
    runs = client.get(f"/api/v1/experiments/{experiment_id}/runs?offset=0&limit=1")
    listing = client.get("/api/v1/experiments?offset=0&limit=10")

    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert estimate.status_code == 200
    assert estimate.json()["cost_fully_configured"] is True
    assert detail.status_code == 200
    assert detail.json()["progress"]["total"] == 1
    assert runs.status_code == 200
    assert runs.json()["total"] == 1
    assert len(runs.json()["items"]) == 1
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
