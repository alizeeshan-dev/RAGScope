from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import corpus_content_hash
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

ALLOWED_TRANSITIONS: dict[CorpusVersionStatus, frozenset[CorpusVersionStatus]] = {
    CorpusVersionStatus.DRAFT: frozenset(
        {CorpusVersionStatus.INDEXING, CorpusVersionStatus.FAILED}
    ),
    CorpusVersionStatus.INDEXING: frozenset(
        {CorpusVersionStatus.READY, CorpusVersionStatus.FAILED}
    ),
    # An unfrozen ready version may revalidate/rebuild the exact same persisted
    # snapshot before freezing; materially changed configurations still require draft/new version.
    CorpusVersionStatus.READY: frozenset(
        {CorpusVersionStatus.INDEXING, CorpusVersionStatus.ARCHIVED}
    ),
    CorpusVersionStatus.FAILED: frozenset(
        {CorpusVersionStatus.DRAFT, CorpusVersionStatus.INDEXING, CorpusVersionStatus.ARCHIVED}
    ),
    CorpusVersionStatus.ARCHIVED: frozenset(),
}


def get_corpus_or_error(session: Session, corpus_id: UUID) -> Corpus:
    corpus = session.get(Corpus, corpus_id)
    if corpus is None:
        raise DomainError(
            "CORPUS_NOT_FOUND", "The requested corpus does not exist.", status_code=404
        )
    return corpus


def get_version_or_error(session: Session, version_id: UUID) -> CorpusVersion:
    version = session.get(CorpusVersion, version_id)
    if version is None:
        raise DomainError(
            "CORPUS_VERSION_NOT_FOUND",
            "The requested corpus version does not exist.",
            status_code=404,
        )
    return version


def assert_version_editable(version: CorpusVersion) -> None:
    if version.frozen_at is not None or version.status is not CorpusVersionStatus.DRAFT:
        raise DomainError(
            "CORPUS_VERSION_IMMUTABLE",
            "Documents and processing configuration may only change on an unfrozen draft version.",
        )


def transition_version(version: CorpusVersion, target: CorpusVersionStatus) -> None:
    if version.frozen_at is not None:
        raise DomainError(
            "CORPUS_VERSION_IMMUTABLE", "A frozen corpus version cannot change state."
        )
    if target not in ALLOWED_TRANSITIONS[version.status]:
        raise DomainError(
            "INVALID_CORPUS_VERSION_TRANSITION",
            f"Corpus version cannot transition from {version.status.value} to {target.value}.",
        )
    version.status = target


def compute_version_hash(session: Session, version: CorpusVersion) -> str:
    document_hashes = session.scalars(
        select(SourceDocument.file_hash)
        .where(SourceDocument.corpus_version_id == version.id)
        .order_by(SourceDocument.file_hash)
    ).all()
    return corpus_content_hash(
        document_hashes=document_hashes,
        parser_configuration=version.parser_configuration,
        chunker_configuration=version.chunker_configuration,
        embedding_configuration=version.embedding_configuration,
    )


def validate_ready_indexes(session: Session, version: CorpusVersion) -> None:
    active_chunker_id = version.chunker_configuration.get("chunker_id")
    chunk_count_query = select(func.count(Chunk.id)).where(Chunk.corpus_version_id == version.id)
    if active_chunker_id:
        chunk_count_query = chunk_count_query.where(Chunk.chunker_id == str(active_chunker_id))
    chunk_count = session.scalar(chunk_count_query) or 0
    documents = session.scalar(
        select(func.count(SourceDocument.id)).where(SourceDocument.corpus_version_id == version.id)
    ) or 0
    if documents == 0 or chunk_count == 0:
        raise DomainError(
            "CORPUS_VERSION_NOT_READY",
            "A corpus version needs parsed documents and chunks before it can be frozen.",
        )

    indexes = session.scalars(
        select(SearchIndex).where(SearchIndex.corpus_version_id == version.id)
    ).all()
    for required_type in (IndexType.LEXICAL, IndexType.DENSE):
        candidates = [
            index
            for index in indexes
            if index.index_type is required_type and index.status is IndexStatus.READY
        ]
        valid = any(
            index.failure_count == 0
            and index.chunk_count == chunk_count
            and index.indexed_count == chunk_count
            and (
                not active_chunker_id
                or index.configuration.get("chunker") == version.chunker_configuration
            )
            for index in candidates
        )
        if not valid:
            raise DomainError(
                "CORPUS_VERSION_NOT_READY",
                f"The {required_type.value} index is missing, incomplete, "
                "or failed integrity checks.",
            )


def freeze_version(session: Session, version: CorpusVersion) -> CorpusVersion:
    if version.frozen_at is not None:
        raise DomainError(
            "CORPUS_VERSION_IMMUTABLE", "The corpus version has already been frozen."
        )
    if version.status is not CorpusVersionStatus.READY:
        raise DomainError(
            "CORPUS_VERSION_NOT_READY", "Only a ready corpus version can be frozen."
        )
    validate_ready_indexes(session, version)
    actual_count = session.scalar(
        select(func.count(SourceDocument.id)).where(SourceDocument.corpus_version_id == version.id)
    ) or 0
    version.document_count = actual_count
    version.content_hash = compute_version_hash(session, version)
    version.frozen_at = datetime.now(UTC)
    session.flush()
    return version
