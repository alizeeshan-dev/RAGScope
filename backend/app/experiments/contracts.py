from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from backend.app.query_runtime.schemas import QueryRunCreate

from .schemas import (
    ExperimentCostEstimate,
    ExperimentDependencySnapshot,
    ExperimentRecord,
    ExperimentRunAttempt,
    ExperimentRunCell,
    QueryExecutionOutcome,
)

QueryExecutor = Callable[[ExperimentRunCell, QueryRunCreate], QueryExecutionOutcome]


class ExperimentDependencyResolver(Protocol):
    def resolve(
        self,
        *,
        corpus_version_id: UUID,
        benchmark_version_id: UUID,
        pipeline_configuration_ids: tuple[UUID, ...],
    ) -> ExperimentDependencySnapshot: ...


class ExperimentStore(Protocol):
    def add_experiment(self, experiment: ExperimentRecord) -> None: ...

    def get_experiment(self, experiment_id: UUID) -> ExperimentRecord | None: ...

    def save_experiment(self, experiment: ExperimentRecord) -> None: ...

    def save_cost_estimate(self, estimate: ExperimentCostEstimate) -> None: ...

    def add_run_cells(self, cells: tuple[ExperimentRunCell, ...]) -> None: ...

    def list_run_cells(self, experiment_id: UUID) -> list[ExperimentRunCell]: ...

    def save_run_cell(self, cell: ExperimentRunCell) -> None: ...

    def add_attempt(self, attempt: ExperimentRunAttempt) -> None: ...

    def save_attempt(self, attempt: ExperimentRunAttempt) -> None: ...

    def get_attempt(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> ExperimentRunAttempt | None: ...

    def find_attempt_outcome(
        self, experiment_run_id: UUID, attempt_number: int
    ) -> QueryExecutionOutcome | None: ...
