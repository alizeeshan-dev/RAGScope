from __future__ import annotations

import time
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings
from backend.app.db.models import Job, JobStatus
from backend.app.indexing.api_schemas import OperationAccepted
from backend.app.jobs.service import complete_job, fail_job, start_job


def receipt(job: Job, *, resource_type: str, resource_id: UUID) -> OperationAccepted:
    return OperationAccepted(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        resource_type=resource_type,
        resource_id=resource_id,
        status_url=f"/api/v1/jobs/{job.id}",
    )


def run_inline_for_bounded_wait(
    session: Session,
    job: Job,
    *,
    settings: Settings,
    artifact_store: LocalArtifactStore,
    timeout_seconds: int = 30,
) -> bool:
    """Wait for completion without turning a production request into unbounded work.

    Tests may enable ``job_api_default_wait`` to execute the handler inline with their
    injected transaction. Production leaves that compatibility flag disabled: an
    explicit ``wait=true`` polls the PostgreSQL worker for at most the requested bound.
    """

    inline_compatibility = settings.job_api_default_wait or settings.database_url.startswith(
        "sqlite"
    )
    if not inline_compatibility:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            session.expire_all()
            current = session.get(Job, job.id)
            if current is not None and current.status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return True
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        session.expire_all()
        return False

    # SQLite has no safe cross-process worker path and is intentionally limited to
    # deterministic local/test compatibility. PostgreSQL production always polls.
    # Local import avoids coupling route module import order to the worker registry.
    from backend.app.jobs.handlers import execute_job

    start_job(job)
    try:
        artifact_ids = execute_job(
            session, job, settings=settings, artifact_store=artifact_store
        )
        complete_job(job, result_artifact_ids=artifact_ids)
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        refreshed_job = session.get(Job, job.id)
        if refreshed_job is not None and refreshed_job.status.value == "running":
            fail_job(
                refreshed_job,
                error_code=getattr(exc, "code", "JOB_EXECUTION_FAILED"),
                error_message=f"Job failed ({type(exc).__name__}).",
            )
            session.commit()
        raise
