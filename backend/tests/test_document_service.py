from __future__ import annotations

import typing
from pathlib import Path

import pytest
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.db.base import Base
from backend.app.db.models import (
    Artifact,
    Corpus,
    CorpusVersion,
    DocumentElement,
    ParseStatus,
)
from backend.app.documents.chunkers import FixedTokenChunker, FixedTokenConfiguration
from backend.app.documents.errors import DocumentParseError, DuplicateDocumentError
from backend.app.documents.parsers.base import ParserConfiguration, ParseResult
from backend.app.documents.service import ChunkService, DocumentService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> typing.Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _version(session: Session) -> CorpusVersion:
    corpus = Corpus(name="Scientific corpus")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(corpus_id=corpus.id, version_label="v1")
    session.add(version)
    session.flush()
    return version


def test_upload_parse_and_chunk_round_trip_is_deterministic(
    session: Session,
    tmp_path: Path,
) -> None:
    version = _version(session)
    service = DocumentService(session, LocalArtifactStore(tmp_path), max_upload_bytes=4096)
    content = (
        b"# Results\n\nA reproducible scientific observation.\n\n## Table\n\n"
        b"| A | B |\n| --- | --- |\n| 1 | 2 |"
    )
    document = service.upload(
        corpus_version_id=version.id,
        filename="paper.md",
        content=content,
        claimed_media_type="text/markdown",
    )
    service.parse(document)
    session.flush()

    assert document.parse_status == ParseStatus.READY
    assert version.document_count == 1
    assert session.scalar(select(DocumentElement).where(DocumentElement.document_id == document.id))
    artifacts = session.scalars(select(Artifact).where(Artifact.document_id == document.id)).all()
    assert {artifact.artifact_type for artifact in artifacts} == {"original", "normalized-document"}

    chunker = FixedTokenChunker(FixedTokenConfiguration(target_tokens=8, overlap_tokens=2))
    first = ChunkService(session).generate_for_version(version, chunker)
    first_hashes = [chunk.content_hash for chunk in first]
    session.flush()
    second = ChunkService(session).generate_for_version(version, chunker)
    session.flush()
    assert [chunk.content_hash for chunk in second] == first_hashes


def test_duplicate_file_is_rejected_within_version(session: Session, tmp_path: Path) -> None:
    version = _version(session)
    service = DocumentService(session, LocalArtifactStore(tmp_path), max_upload_bytes=4096)
    service.upload(
        corpus_version_id=version.id,
        filename="paper.txt",
        content=b"same bytes",
        claimed_media_type="text/plain",
    )
    with pytest.raises(DuplicateDocumentError):
        service.upload(
            corpus_version_id=version.id,
            filename="paper.txt",
            content=b"same bytes",
            claimed_media_type="text/plain",
        )


class FailingParser:
    parser_id = "failing"
    parser_version = "1"
    media_types = frozenset({"text/plain"})

    def parse(
        self,
        content: bytes,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult:
        raise DocumentParseError("synthetic failure")


def test_parse_failure_never_leaves_ready_or_partial_elements(
    session: Session,
    tmp_path: Path,
) -> None:
    version = _version(session)
    service = DocumentService(session, LocalArtifactStore(tmp_path), max_upload_bytes=4096)
    document = service.upload(
        corpus_version_id=version.id,
        filename="paper.txt",
        content=b"content",
        claimed_media_type="text/plain",
    )
    with pytest.raises(DocumentParseError):
        service.parse(document, parser=FailingParser())

    assert document.parse_status == ParseStatus.FAILED
    assert session.scalars(
        select(DocumentElement).where(DocumentElement.document_id == document.id)
    ).all() == []
    assert document.parse_warnings[0]["code"] == "DOCUMENT_PARSE_FAILED"
