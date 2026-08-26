from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import Job, JobStatus


def create_job(
    session: Session,
    *,
    job_type: str,
    input_reference: dict[str, Any],
    idempotency_key: str | None = None,
    progress_total: int = 0,
) -> tuple[Job, bool]:
    """Create a queued job, returning an existing row for a repeated idempotency key."""

    if idempotency_key:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    job = Job(
        job_type=job_type,
        input_reference=input_reference,
        idempotency_key=idempotency_key,
        progress_total=progress_total,
    )
    session.add(job)
    session.flush()
    return job, True


def start_job(job: Job) -> None:
    if job.status not in {JobStatus.QUEUED, JobStatus.FAILED}:
        raise DomainError("INVALID_JOB_TRANSITION", "Only queued or failed jobs can start.")
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.error_code = None
    job.error_message = None


def complete_job(job: Job, *, result_artifact_ids: list[str] | None = None) -> None:
    if job.status is not JobStatus.RUNNING:
        raise DomainError("INVALID_JOB_TRANSITION", "Only a running job can complete.")
    job.status = JobStatus.SUCCEEDED
    job.progress_current = job.progress_total
    job.finished_at = datetime.now(UTC)
    job.result_artifact_ids = result_artifact_ids or []


def fail_job(job: Job, *, error_code: str, error_message: str) -> None:
    if job.status is not JobStatus.RUNNING:
        raise DomainError("INVALID_JOB_TRANSITION", "Only a running job can fail.")
    job.status = JobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    job.retry_count += 1
    job.error_code = error_code
    job.error_message = error_message
