from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import Job, JobAttempt, JobAttemptStatus, JobStatus


def create_job(
    session: Session,
    *,
    job_type: str,
    input_reference: dict[str, Any],
    idempotency_key: str | None = None,
    progress_total: int = 0,
    max_attempts: int = 3,
) -> tuple[Job, bool]:
    """Create a queued job, returning an existing row for a repeated idempotency key."""

    if idempotency_key:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.status is JobStatus.FAILED and existing.retry_count < existing.max_attempts:
                existing.status = JobStatus.QUEUED
                existing.available_at = datetime.now(UTC)
                existing.finished_at = None
                existing.error_code = None
                existing.error_message = None
            return existing, False
    job = Job(
        job_type=job_type,
        input_reference=input_reference,
        idempotency_key=idempotency_key,
        progress_total=progress_total,
        available_at=datetime.now(UTC),
        max_attempts=max_attempts,
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


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    job_types: Sequence[str] | None = None,
) -> Job | None:
    """Atomically lease the next available job.

    PostgreSQL workers use ``SKIP LOCKED`` so multiple worker processes cannot
    execute the same operation. SQLite ignores the locking clause, which is
    sufficient for deterministic single-worker unit tests.
    """

    now = datetime.now(UTC)
    conditions = [
        Job.cancellation_requested_at.is_(None),
        Job.retry_count < Job.max_attempts,
        or_(
            and_(
                Job.status == JobStatus.QUEUED,
                or_(Job.available_at.is_(None), Job.available_at <= now),
            ),
            and_(
                Job.status == JobStatus.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
            ),
        ),
    ]
    if job_types:
        conditions.append(Job.job_type.in_(tuple(job_types)))
    candidate = session.scalar(
        select(Job)
        .where(*conditions)
        .order_by(Job.available_at, Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if candidate is None:
        return None

    if candidate.status is JobStatus.RUNNING:
        previous = session.scalar(
            select(JobAttempt)
            .where(
                JobAttempt.job_id == candidate.id,
                JobAttempt.status == JobAttemptStatus.RUNNING,
            )
            .order_by(JobAttempt.attempt_number.desc())
            .limit(1)
        )
        if previous is not None:
            previous.status = JobAttemptStatus.INTERRUPTED
            previous.finished_at = now
            previous.error_code = "JOB_LEASE_EXPIRED"
            previous.error_message = "Worker lease expired before completion."
        candidate.retry_count += 1

    attempt_number = int(
        session.scalar(
            select(func.coalesce(func.max(JobAttempt.attempt_number), 0)).where(
                JobAttempt.job_id == candidate.id
            )
        )
        or 0
    ) + 1
    candidate.status = JobStatus.RUNNING
    candidate.started_at = candidate.started_at or now
    candidate.finished_at = None
    candidate.lease_owner = worker_id
    candidate.heartbeat_at = now
    candidate.lease_expires_at = now + timedelta(seconds=lease_seconds)
    candidate.error_code = None
    candidate.error_message = None
    session.add(
        JobAttempt(
            job_id=candidate.id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            status=JobAttemptStatus.RUNNING,
            started_at=now,
        )
    )
    session.flush()
    return candidate


def heartbeat_job(
    job: Job,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> None:
    if job.status is not JobStatus.RUNNING or job.lease_owner != worker_id:
        raise DomainError("INVALID_JOB_LEASE", "The worker does not own this job lease.")
    now = datetime.now(UTC)
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)


def request_job_cancellation(job: Job) -> None:
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise DomainError("INVALID_JOB_TRANSITION", "A terminal job cannot be cancelled.")
    job.cancellation_requested_at = datetime.now(UTC)
    if job.status is JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)


def cancellation_requested(job: Job) -> bool:
    return job.cancellation_requested_at is not None


def _finish_active_attempt(
    job: Job,
    *,
    status: JobAttemptStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    active = next(
        (value for value in reversed(job.attempts) if value.status is JobAttemptStatus.RUNNING),
        None,
    )
    if active is not None:
        active.status = status
        active.finished_at = datetime.now(UTC)
        active.error_code = error_code
        active.error_message = error_message
    job.lease_owner = None
    job.lease_expires_at = None


def complete_job(job: Job, *, result_artifact_ids: list[str] | None = None) -> None:
    if job.status is not JobStatus.RUNNING:
        raise DomainError("INVALID_JOB_TRANSITION", "Only a running job can complete.")
    job.status = JobStatus.SUCCEEDED
    job.progress_current = job.progress_total
    job.finished_at = datetime.now(UTC)
    job.result_artifact_ids = result_artifact_ids or []
    _finish_active_attempt(job, status=JobAttemptStatus.SUCCEEDED)


def fail_job(job: Job, *, error_code: str, error_message: str) -> None:
    if job.status is not JobStatus.RUNNING:
        raise DomainError("INVALID_JOB_TRANSITION", "Only a running job can fail.")
    job.status = JobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    job.retry_count += 1
    job.error_code = error_code
    job.error_message = error_message
    _finish_active_attempt(
        job,
        status=JobAttemptStatus.FAILED,
        error_code=error_code,
        error_message=error_message,
    )


def cancel_job(job: Job) -> None:
    if job.status is not JobStatus.RUNNING:
        raise DomainError("INVALID_JOB_TRANSITION", "Only a running job can be cancelled.")
    job.status = JobStatus.CANCELLED
    job.finished_at = datetime.now(UTC)
    _finish_active_attempt(job, status=JobAttemptStatus.CANCELLED)
