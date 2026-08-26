import pytest
from backend.app.core.errors import DomainError
from backend.app.corpora.service import freeze_version, transition_version
from backend.app.db.models import (
    Chunk,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    IndexStatus,
    IndexType,
    SearchIndex,
    SourceDocument,
)
from sqlalchemy.orm import Session


def build_ready_version(session: Session) -> CorpusVersion:
    corpus = Corpus(name="C")
    version = CorpusVersion(
        corpus=corpus,
        version_label="v1",
        parser_configuration={"version": "1"},
        chunker_configuration={"strategy": "fixed", "size": 100},
        embedding_configuration={"provider": "fake", "dimension": 64},
    )
    document = SourceDocument(
        corpus_version=version,
        title="Synthetic",
        file_hash="a" * 64,
        mime_type="text/plain",
    )
    session.add_all([corpus, version, document])
    session.flush()
    chunk = Chunk(
        document=document,
        corpus_version=version,
        chunker_id="fixed-v1",
        sequence_number=0,
        text="reproducible evidence",
        token_count=2,
        content_hash="b" * 64,
    )
    session.add(chunk)
    session.flush()
    for index_type in (IndexType.LEXICAL, IndexType.DENSE):
        session.add(
            SearchIndex(
                corpus_version=version,
                index_type=index_type,
                status=IndexStatus.READY,
                configuration_hash=("c" if index_type is IndexType.LEXICAL else "d") * 64,
                chunk_count=1,
                indexed_count=1,
            )
        )
    transition_version(version, CorpusVersionStatus.INDEXING)
    transition_version(version, CorpusVersionStatus.READY)
    session.flush()
    return version


def test_state_transitions_are_explicit(session: Session) -> None:
    corpus = Corpus(name="C")
    version = CorpusVersion(corpus=corpus, version_label="v1")
    session.add_all([corpus, version])
    session.flush()
    with pytest.raises(DomainError, match="cannot transition") as raised:
        transition_version(version, CorpusVersionStatus.READY)
    assert raised.value.code == "INVALID_CORPUS_VERSION_TRANSITION"


def test_freeze_calculates_hash_and_blocks_subsequent_mutation(session: Session) -> None:
    version = build_ready_version(session)
    freeze_version(session, version)
    session.commit()
    assert version.content_hash is not None
    assert len(version.content_hash) == 64
    assert version.document_count == 1

    version.version_label = "tampered"
    with pytest.raises(DomainError) as raised:
        session.flush()
    assert raised.value.code == "CORPUS_VERSION_IMMUTABLE"
    session.rollback()


def test_freeze_requires_complete_version_isolated_indexes(session: Session) -> None:
    version = build_ready_version(session)
    version.indexes[0].indexed_count = 0
    with pytest.raises(DomainError) as raised:
        freeze_version(session, version)
    assert raised.value.code == "CORPUS_VERSION_NOT_READY"


def test_frozen_version_rejects_state_change(session: Session) -> None:
    version = build_ready_version(session)
    freeze_version(session, version)
    session.flush()
    with pytest.raises(DomainError) as raised:
        transition_version(version, CorpusVersionStatus.ARCHIVED)
    assert raised.value.code == "CORPUS_VERSION_IMMUTABLE"
