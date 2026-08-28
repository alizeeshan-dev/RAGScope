"""PostgreSQL-backed RAGScope worker process."""

from __future__ import annotations

import logging
import os
import socket
import time
from uuid import uuid4

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, correlation_id, event
from backend.app.db.models import JobStatus
from backend.app.db.session import SessionLocal
from backend.app.jobs.handlers import execute_job
from backend.app.jobs.service import (
    cancel_job,
    cancellation_requested,
    claim_next_job,
    complete_job,
    fail_job,
)

logger = logging.getLogger("ragscope.worker")


def run_once(*, worker_id: str, lease_seconds: int = 120) -> bool:
    settings = get_settings()
    with SessionLocal() as session:
        job = claim_next_job(
            session, worker_id=worker_id, lease_seconds=lease_seconds
        )
        if job is None:
            session.rollback()
            return False
        job_id = job.id
        session.commit()
        correlation_id.set(f"job:{job_id}")
        event(logger, "job.started", job_id=str(job_id), job_type=job.job_type)
        try:
            session.refresh(job)
            if cancellation_requested(job):
                cancel_job(job)
            else:
                artifact_ids = execute_job(
                    session,
                    job,
                    settings=settings,
                    artifact_store=LocalArtifactStore(settings.artifact_root),
                )
                session.refresh(job)
                if cancellation_requested(job):
                    cancel_job(job)
                elif job.status is JobStatus.RUNNING:
                    complete_job(job, result_artifact_ids=artifact_ids)
            session.commit()
            event(logger, "job.finished", job_id=str(job_id), status=job.status.value)
        except Exception as exc:  # noqa: BLE001 - worker failure boundary
            session.rollback()
            job = session.get(type(job), job_id)
            if job is not None and job.status is JobStatus.RUNNING:
                fail_job(
                    job,
                    error_code=getattr(exc, "code", "JOB_EXECUTION_FAILED"),
                    error_message=f"Job failed ({type(exc).__name__}).",
                )
                session.commit()
            event(
                logger,
                "job.failed",
                job_id=str(job_id),
                error_code=getattr(exc, "code", "JOB_EXECUTION_FAILED"),
                exception_type=type(exc).__name__,
            )
        return True


def main() -> None:
    configure_logging()
    worker_id = os.getenv(
        "RAGSCOPE_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    )
    poll_seconds = float(os.getenv("RAGSCOPE_WORKER_POLL_SECONDS", "1"))
    lease_seconds = int(os.getenv("RAGSCOPE_WORKER_LEASE_SECONDS", "120"))
    event(logger, "worker.ready", worker_id=worker_id)
    while True:
        if not run_once(worker_id=worker_id, lease_seconds=lease_seconds):
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
