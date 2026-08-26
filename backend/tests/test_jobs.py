from backend.app.db.models import JobStatus
from backend.app.jobs.service import complete_job, create_job, fail_job, start_job
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
