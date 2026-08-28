from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings
from backend.app.db.models import JobAttemptStatus, JobStatus
from backend.app.jobs import api_contract
from backend.app.jobs.api_contract import run_inline_for_bounded_wait
from backend.app.jobs.service import (
    cancel_job,
    claim_next_job,
    complete_job,
    create_job,
    fail_job,
    heartbeat_job,
    request_job_cancellation,
    start_job,
)
from sqlalchemy.orm import Session


def test_job_creation_is_idempotent(session: Session) -> None:
    first, created = create_job(
        session,
        job_type="document_parse",
        input_reference={"document_id": "doc-1"},
        idempotency_key="parse:doc-1:config-1",
    )
    second, created_again = create_job(
        session,
        job_type="document_parse",
        input_reference={"document_id": "doc-1"},
        idempotency_key="parse:doc-1:config-1",
    )
    assert created is True
    assert created_again is False
    assert first.id == second.id


def test_job_failure_and_retry_metadata(session: Session) -> None:
    job, _ = create_job(
        session, job_type="index_build", input_reference={"version_id": "v1"}
    )
    start_job(job)
    fail_job(job, error_code="INDEX_BUILD_FAILED", error_message="synthetic failure")
    assert job.status is JobStatus.FAILED
    assert job.retry_count == 1
    start_job(job)
    complete_job(job, result_artifact_ids=["artifact-1"])
    assert job.status == JobStatus.SUCCEEDED  # type: ignore[comparison-overlap]
    assert job.result_artifact_ids == ["artifact-1"]


def test_worker_claim_lease_heartbeat_and_cancel(session: Session) -> None:
    queued, _ = create_job(
        session, job_type="document_parsing", input_reference={"document_id": "doc"}
    )
    session.commit()
    claimed = claim_next_job(session, worker_id="worker-a", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempts[0].status is JobAttemptStatus.RUNNING
    first_expiry = claimed.lease_expires_at
    heartbeat_job(claimed, worker_id="worker-a", lease_seconds=60)
    assert claimed.lease_expires_at is not None
    assert first_expiry is not None and claimed.lease_expires_at > first_expiry
    request_job_cancellation(claimed)
    cancel_job(claimed)
    assert claimed.status is JobStatus.CANCELLED
    assert claimed.attempts[0].status is JobAttemptStatus.CANCELLED


def test_expired_worker_lease_is_reclaimed_with_attempt_history(session: Session) -> None:
    queued, _ = create_job(
        session, job_type="build_indexes", input_reference={"corpus_version_id": "version"}
    )
    session.commit()
    first = claim_next_job(session, worker_id="worker-a", lease_seconds=30)
    assert first is not None
    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    reclaimed = claim_next_job(session, worker_id="worker-b", lease_seconds=30)
    assert reclaimed is not None and reclaimed.id == queued.id
    assert [attempt.status for attempt in reclaimed.attempts] == [
        JobAttemptStatus.INTERRUPTED,
        JobAttemptStatus.RUNNING,
    ]
    assert reclaimed.retry_count == 1


def test_production_bounded_wait_returns_without_executing_job_inline(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, _ = create_job(
        session, job_type="document_parsing", input_reference={"document_id": "doc"}
    )
    session.commit()
    ticks = iter((100.0, 102.0))
    monkeypatch.setattr(api_contract.time, "monotonic", lambda: next(ticks))

    completed = run_inline_for_bounded_wait(
        session,
        job,
        settings=Settings(
            _env_file=None,
            database_url="postgresql+psycopg://test:test@localhost/test",
            artifact_root=tmp_path / "artifacts",
        ),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        timeout_seconds=1,
    )

    assert completed is False
    assert job.status is JobStatus.QUEUED
