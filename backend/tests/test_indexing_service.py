from __future__ import annotations

from collections.abc import Generator, Sequence
from uuid import uuid4

import pytest
from backend.app.db.base import Base
from backend.app.db.models import (
    Chunk,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    IndexEntry,
    IndexStatus,
    IndexType,
    Job,
    SearchIndex,
    SourceDocument,
)
from backend.app.indexing.jobs import IndexingJobRunner
from backend.app.indexing.service import IndexingService
from backend.app.providers.fake import DeterministicEmbeddingProvider
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    Base.metadata.drop_all(engine)


def add_version(session: Session, label: str, texts: Sequence[str]) -> CorpusVersion:
    corpus = Corpus(name=f"Corpus {label}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label=label,
        embedding_configuration={
            "provider": "fake",
            "model": "fake-hash-embedding-v1",
            "dimension": 32,
            "preprocessing_version": "hashed-bow-v1",
        },
    )
    session.add(version)
    session.flush()
    document = SourceDocument(
        corpus_version_id=version.id,
        title="Synthetic fixture",
        file_hash=uuid4().hex + uuid4().hex,
        mime_type="text/plain",
    )
    session.add(document)
    session.flush()
    for sequence, text in enumerate(texts):
        session.add(
            Chunk(
                document_id=document.id,
                corpus_version_id=version.id,
                chunker_id="test-v1",
                sequence_number=sequence,
                text=text,
                token_count=len(text.split()),
                content_hash=(f"{sequence:064x}"),
            )
        )
    session.flush()
    return version


def service(session: Session) -> IndexingService:
    return IndexingService(session, DeterministicEmbeddingProvider(dimension=32), batch_size=1)


def test_build_search_and_retry_are_deterministic_and_idempotent(session: Session) -> None:
    version = add_version(
        session,
        "v1",
        ["satellite precipitation dataset", "protein folding microscopy"],
    )
    indexing = service(session)

    first_indexes = indexing.build_all(version.id)
    first_ids = [index.id for index in first_indexes]
    first_entry_count = session.scalar(select(func.count(IndexEntry.id)))
    second_indexes = indexing.build_all(version.id)

    assert version.status == CorpusVersionStatus.READY
    assert [index.id for index in second_indexes] == first_ids
    assert session.scalar(select(func.count(IndexEntry.id))) == first_entry_count == 4
    assert indexing.lexical_search(version.id, "precipitation", top_k=2)[0].text.startswith(
        "satellite"
    )
    assert indexing.dense_search(version.id, "precipitation satellite", top_k=2)[0].text.startswith(
        "satellite"
    )
    assert all(report.valid for report in indexing.status(version.id))


def test_search_and_integrity_are_corpus_version_isolated(session: Session) -> None:
    first = add_version(session, "first", ["alpha evidence"])
    second = add_version(session, "second", ["secret beta evidence"])
    first_service = service(session)
    first_service.build_all(first.id)
    service(session).build_all(second.id)

    assert first_service.lexical_search(first.id, "secret", top_k=10) == []
    assert all(
        result.corpus_version_id == str(first.id)
        for result in first_service.dense_search(first.id, "secret", top_k=10)
    )

    lexical_index = session.scalar(
        select(SearchIndex).where(
            SearchIndex.corpus_version_id == first.id,
            SearchIndex.index_type == IndexType.LEXICAL,
        )
    )
    foreign_chunk = session.scalar(select(Chunk).where(Chunk.corpus_version_id == second.id))
    assert lexical_index is not None and foreign_chunk is not None
    session.add(
        IndexEntry(
            index_id=lexical_index.id,
            chunk_id=foreign_chunk.id,
            searchable_text=foreign_chunk.text,
        )
    )
    session.flush()
    report = first_service.verify(lexical_index.id)
    assert not report.valid
    assert report.foreign_chunk_count == 1


def test_index_uses_only_active_chunker_snapshot(session: Session) -> None:
    version = add_version(session, "chunkers", ["active structure evidence"])
    version.chunker_configuration = {
        "chunker_id": "test-v1",
        "configuration": {"target_tokens": 128},
    }
    document = version.documents[0]
    session.add(
        Chunk(
            document_id=document.id,
            corpus_version_id=version.id,
            chunker_id="fixed-token-v1",
            sequence_number=0,
            text="inactive fixed distractor",
            token_count=3,
            content_hash="f" * 64,
        )
    )
    session.flush()

    indexing = service(session)
    indexes = indexing.build_all(version.id)

    assert all(index.chunk_count == 1 for index in indexes)
    assert indexing.lexical_search(version.id, "distractor", top_k=10) == []
    assert indexing.lexical_search(version.id, "structure", top_k=10)[0].text.startswith("active")


def test_indexing_job_has_stable_idempotency_key(session: Session) -> None:
    version = add_version(session, "job", ["repeatable indexing"])
    runner = IndexingJobRunner(session, service(session))
    first = runner.run(version.id)
    second = runner.run(version.id)
    assert first.id == second.id
    assert session.scalar(select(func.count(Job.id))) == 1
    assert first.progress_current == first.progress_total == 2


class BrokenEmbeddingProvider(DeterministicEmbeddingProvider):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


def test_partial_dense_failure_does_not_mark_version_ready(session: Session) -> None:
    version = add_version(session, "failure", ["one chunk"])
    indexing = IndexingService(session, BrokenEmbeddingProvider(dimension=32))
    with pytest.raises(ValueError, match="invalid dimension"):
        indexing.build_all(version.id)

    assert version.status == CorpusVersionStatus.FAILED
    statuses = {
        index.index_type: index.status
        for index in session.scalars(select(SearchIndex))
    }
    assert statuses[IndexType.LEXICAL] == IndexStatus.READY
    assert statuses[IndexType.DENSE] == IndexStatus.FAILED
