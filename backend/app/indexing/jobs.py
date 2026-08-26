"""Database-backed inline indexing job runner with stable idempotency keys."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Job, JobStatus

from .configuration import canonical_configuration_hash
from .service import IndexingService


class IndexingJobRunner:
    """Runs locally now while retaining a queue-compatible persisted contract."""

    def __init__(self, session: Session, indexing: IndexingService) -> None:
        self.session = session
        self.indexing = indexing

    def get_or_create(self, corpus_version_id: UUID) -> Job:
        configuration = {
            "corpus_version_id": str(corpus_version_id),
            "chunker": dict(self.indexing._version(corpus_version_id).chunker_configuration),
            "provider": self.indexing.embedding_provider.provider_id,
            "model": self.indexing.embedding_provider.model_id,
            "dimension": self.indexing.embedding_provider.dimension,
            "preprocessing_version": self.indexing.embedding_provider.preprocessing_version,
        }
        key = f"index:{corpus_version_id}:{canonical_configuration_hash(configuration)}"
        existing = self.session.scalar(select(Job).where(Job.idempotency_key == key))
        if existing is not None:
            return existing
        job = Job(
            job_type="build_indexes",
            input_reference=configuration,
            idempotency_key=key,
            progress_current=0,
            progress_total=2,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def run(self, corpus_version_id: UUID) -> Job:
        job = self.get_or_create(corpus_version_id)
        if job.status == JobStatus.SUCCEEDED:
            # The operation is idempotent, but do not trust a stale succeeded job:
            # verify persisted indexes before returning it.
            reports = self.indexing.status(corpus_version_id)
            if len(reports) >= 2 and all(report.valid for report in reports):
                return job
        if job.status == JobStatus.RUNNING:
            # Duplicate dispatch protection. A real worker can reclaim stale jobs
            # using a lease; local Chunk 1 execution is synchronous.
            return job
        if job.status == JobStatus.FAILED:
            job.retry_count += 1
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        job.progress_current = 0
        self.session.flush()
        try:
            self.indexing.build_all(corpus_version_id)
            job.progress_current = 2
            job.progress_total = 2
            job.status = JobStatus.SUCCEEDED
            # Index records are queryable through index-status; do not mislabel
            # their IDs as artifact IDs when this operation produced no artifact.
            job.result_artifact_ids = []
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error_code = getattr(exc, "code", "INDEX_BUILD_FAILED")
            job.error_message = str(exc)[:2000]
            raise
        finally:
            job.finished_at = datetime.now(UTC)
            self.session.flush()
        return job
