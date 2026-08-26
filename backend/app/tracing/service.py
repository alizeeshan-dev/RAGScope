"""Persistence boundary for compact observable execution spans."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from time import perf_counter
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import Artifact, TraceSpan, TraceSpanStatus

from .redaction import configured_sensitive_values, redact


class TraceRecordingError(RuntimeError):
    """Raised when trace instrumentation cannot be persisted safely."""


class SpanRecorder:
    """Create ordered spans for one QueryRun without committing its transaction."""

    def __init__(
        self,
        session: Session,
        query_run_id: UUID,
        *,
        sensitive_values: tuple[str, ...] | None = None,
    ) -> None:
        self.session = session
        self.query_run_id = query_run_id
        self.sensitive_values = (
            tuple(configured_sensitive_values())
            if sensitive_values is None
            else sensitive_values
        )

    def start(
        self,
        span_type: str,
        name: str,
        *,
        parent: TraceSpan | SpanHandle | UUID | None = None,
        input_summary: dict[str, Any] | None = None,
        configuration_snapshot: dict[str, Any] | None = None,
    ) -> SpanHandle:
        """Persist a running span; its sequence is allocated in actual start order."""

        try:
            span = TraceSpan(
                query_run_id=self.query_run_id,
                parent_span_id=_parent_id(parent),
                sequence_number=self._next_sequence(),
                span_type=span_type,
                name=name,
                status=TraceSpanStatus.RUNNING,
                started_at=datetime.now(UTC),
                finished_at=None,
                latency_ms=None,
                input_summary=self._safe(input_summary),
                output_summary={},
                configuration_snapshot=self._safe(configuration_snapshot),
                error_code=None,
                artifact_ids=[],
            )
            self.session.add(span)
            self.session.flush()
            return SpanHandle(self, span)
        except Exception as exc:
            raise TraceRecordingError("Could not start observable trace span") from exc

    def span(
        self,
        span_type: str,
        name: str,
        **metadata: Any,
    ) -> SpanHandle:
        """Context-manager alias for :meth:`start`."""

        return self.start(span_type, name, **metadata)

    def _next_sequence(self) -> int:
        current = self.session.scalar(
            select(func.coalesce(func.max(TraceSpan.sequence_number), 0)).where(
                TraceSpan.query_run_id == self.query_run_id
            )
        )
        return int(current or 0) + 1

    def _safe(self, value: dict[str, Any] | None) -> dict[str, Any]:
        safe = redact(value or {}, sensitive_values=self.sensitive_values)
        if not isinstance(safe, dict):  # pragma: no cover - defensive type boundary
            raise TraceRecordingError("Trace metadata must be an object")
        return safe


class SpanHandle(AbstractContextManager["SpanHandle"]):
    """Finalize one span exactly once and expose its identity to child stages."""

    def __init__(self, recorder: SpanRecorder, record: TraceSpan) -> None:
        self.recorder = recorder
        self.record = record
        self._started_counter = perf_counter()
        self._finished = False

    @property
    def id(self) -> UUID:
        return self.record.id

    def __enter__(self) -> SpanHandle:
        return self

    def succeed(
        self,
        output_summary: dict[str, Any] | None = None,
        *,
        artifact_ids: list[UUID | str] | tuple[UUID | str, ...] = (),
    ) -> TraceSpan:
        return self._finish(
            status="succeeded",
            output_summary=output_summary,
            error_code=None,
            artifact_ids=artifact_ids,
        )

    def fail(
        self,
        error_code: str,
        output_summary: dict[str, Any] | None = None,
        *,
        artifact_ids: list[UUID | str] | tuple[UUID | str, ...] = (),
    ) -> TraceSpan:
        if not error_code:
            raise ValueError("A failed trace span requires a stable error code")
        return self._finish(
            status="failed",
            output_summary=output_summary,
            error_code=error_code,
            artifact_ids=artifact_ids,
        )

    def add_artifact(self, artifact_id: UUID | str) -> None:
        if self._finished:
            raise TraceRecordingError("A finished trace span is immutable")
        values = [*self.record.artifact_ids, str(artifact_id)]
        self.record.artifact_ids = list(dict.fromkeys(values))
        self._link_artifacts([artifact_id])
        self.recorder.session.flush()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del traceback
        if self._finished:
            return None
        if exc_type is None:
            self.succeed()
            return None
        # Do not persist exception messages: provider exceptions can embed request
        # payloads or credentials. The orchestrator may replace this with a more
        # specific stable code by calling ``fail`` before leaving the block.
        self.fail(
            "UNEXPECTED_STAGE_FAILURE",
            {"exception_type": exc_type.__name__, "exception_present": exc_value is not None},
        )
        return None

    def _finish(
        self,
        *,
        status: str,
        output_summary: dict[str, Any] | None,
        error_code: str | None,
        artifact_ids: list[UUID | str] | tuple[UUID | str, ...],
    ) -> TraceSpan:
        if self._finished:
            raise TraceRecordingError("A trace span may only be finalized once")
        try:
            finished_at = datetime.now(UTC)
            self.record.status = TraceSpanStatus(status)
            self.record.finished_at = finished_at
            # The monotonic clock is immune to wall-clock adjustments and works even
            # when SQLite returns a timezone-naive persisted timestamp.
            self.record.latency_ms = max(0, round((perf_counter() - self._started_counter) * 1000))
            self.record.output_summary = self.recorder._safe(output_summary)
            self.record.error_code = error_code
            existing = [str(item) for item in self.record.artifact_ids]
            supplied = [str(item) for item in artifact_ids]
            self.record.artifact_ids = list(dict.fromkeys([*existing, *supplied]))
            self._link_artifacts(artifact_ids)
            self.recorder.session.flush()
            self._finished = True
            return self.record
        except Exception as exc:
            raise TraceRecordingError("Could not finalize observable trace span") from exc

    def _link_artifacts(
        self, artifact_ids: list[UUID | str] | tuple[UUID | str, ...]
    ) -> None:
        for raw_id in artifact_ids:
            try:
                artifact_id = UUID(str(raw_id))
            except ValueError:
                continue
            artifact = self.recorder.session.get(Artifact, artifact_id)
            if artifact is None:
                # Span artifact lists can also reference external immutable stores.
                # When an RAGScope Artifact row exists, its provenance is mandatory.
                continue
            if artifact.query_run_id not in {None, self.recorder.query_run_id}:
                raise TraceRecordingError("Artifact belongs to a different query run")
            artifact.query_run_id = self.recorder.query_run_id
            artifact.trace_span_id = self.record.id


def _parent_id(parent: TraceSpan | SpanHandle | UUID | None) -> UUID | None:
    if parent is None:
        return None
    if isinstance(parent, SpanHandle):
        return parent.id
    if isinstance(parent, UUID):
        return parent
    return parent.id
