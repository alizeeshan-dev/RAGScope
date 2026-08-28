from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.db.models import (
    Chunk,
    Corpus,
    CorpusVersion,
    ParseStatus,
    SourceDocument,
)
from backend.app.db.session import build_engine
from backend.app.indexing.service import IndexingService
from backend.app.jobs.service import claim_next_job, create_job
from backend.app.providers.fake import DeterministicEmbeddingProvider
from sqlalchemy.orm import Session


@pytest.mark.postgres
def test_postgres_pgvector_and_fts_retrieval_are_migration_valid() -> None:
    database_url = os.getenv("RAGSCOPE_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("RAGSCOPE_POSTGRES_TEST_URL is not configured")
    engine = build_engine(database_url)
    assert engine.dialect.name == "postgresql"
    suffix = uuid4().hex
    with Session(engine) as session:
        corpus = Corpus(name=f"postgres-validity-{suffix}")
        session.add(corpus)
        session.flush()
        provider = DeterministicEmbeddingProvider(dimension=16)
        version = CorpusVersion(
            corpus_id=corpus.id,
            version_label="v1",
            content_hash="a" * 64,
            embedding_configuration={
                "provider": provider.provider_id,
                "model": provider.model_id,
                "dimension": provider.dimension,
                "preprocessing_version": provider.preprocessing_version,
            },
        )
        session.add(version)
        session.flush()
        document = SourceDocument(
            corpus_version_id=version.id,
            title="PostgreSQL validity paper",
            file_hash=uuid4().hex.ljust(64, "0"),
            mime_type="application/pdf",
            parse_status=ParseStatus.READY,
        )
        session.add(document)
        session.flush()
        session.add_all(
            [
                Chunk(
                    document_id=document.id,
                    corpus_version_id=version.id,
                    chunker_id="fixed-v1",
                    sequence_number=1,
                    text="Quasar observations use a calibrated spectrograph.",
                    token_count=7,
                    content_hash=uuid4().hex.ljust(64, "0"),
                ),
                Chunk(
                    document_id=document.id,
                    corpus_version_id=version.id,
                    chunker_id="fixed-v1",
                    sequence_number=2,
                    text="Participant annotations were collected independently.",
                    token_count=6,
                    content_hash=uuid4().hex.ljust(64, "0"),
                ),
            ]
        )
        service = IndexingService(session, provider)
        lexical, dense = service.build_all(version.id)
        version.frozen_at = datetime.now(UTC)
        session.commit()

        assert service.verify(lexical.id).valid
        assert service.verify(dense.id).valid
        lexical_rows = service.lexical_search(version.id, "calibrated spectrograph", top_k=1)
        dense_rows = service.dense_search(version.id, "quasar spectrograph", top_k=2)
        assert lexical_rows and "spectrograph" in lexical_rows[0].text
        assert len(dense_rows) == 2
        assert all(row.corpus_version_id == str(version.id) for row in dense_rows)

    engine.dispose()


@pytest.mark.postgres
def test_postgres_workers_skip_locked_jobs() -> None:
    database_url = os.getenv("RAGSCOPE_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("RAGSCOPE_POSTGRES_TEST_URL is not configured")
    engine = build_engine(database_url)
    suffix = uuid4().hex
    job_type = f"postgres-concurrency-{suffix}"
    with Session(engine) as setup:
        first, _ = create_job(
            setup,
            job_type=job_type,
            input_reference={"fixture": 1},
            idempotency_key=f"postgres-concurrency:{suffix}:1",
        )
        second, _ = create_job(
            setup,
            job_type=job_type,
            input_reference={"fixture": 2},
            idempotency_key=f"postgres-concurrency:{suffix}:2",
        )
        expected = {first.id, second.id}
        setup.commit()
    with Session(engine) as worker_a, Session(engine) as worker_b:
        claimed_a = claim_next_job(
            worker_a, worker_id="worker-a", job_types=(job_type,)
        )
        assert claimed_a is not None
        claimed_b = claim_next_job(
            worker_b, worker_id="worker-b", job_types=(job_type,)
        )
        assert claimed_b is not None
        assert {claimed_a.id, claimed_b.id} == expected
        worker_a.rollback()
        worker_b.rollback()
    engine.dispose()
