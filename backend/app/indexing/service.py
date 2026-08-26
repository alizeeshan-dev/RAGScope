"""Persistent, version-isolated lexical and dense index service."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Select, cast, func, literal, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Chunk,
    CorpusVersion,
    CorpusVersionStatus,
    IndexEntry,
    IndexStatus,
    IndexType,
    SearchIndex,
)
from backend.app.providers.base import EmbeddingProvider

from .configuration import (
    canonical_configuration_hash,
    dense_configuration,
    lexical_configuration,
)
from .errors import (
    CorpusVersionImmutable,
    CorpusVersionNotFound,
    EmbeddingProviderFailure,
    IndexConfigurationMismatch,
    IndexIntegrityError,
    IndexNotReady,
)
from .schemas import IndexIntegrityReport, RetrievalResult, SearchFilters
from .scoring import (
    SearchRecord,
    portable_dense_search,
    portable_lexical_search,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IndexingService:
    """Build/search both index types without allowing cross-version entries.

    Calls are safe to retry: the configuration key is stable and each
    ``(index_id, chunk_id)`` entry is unique. Existing valid entries are reused.
    The caller controls transaction commits; a local job runner may commit each
    completed invocation.
    """

    def __init__(
        self,
        session: Session,
        embedding_provider: EmbeddingProvider,
        *,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.session = session
        self.embedding_provider = embedding_provider
        self.batch_size = batch_size

    def build_all(self, corpus_version_id: UUID) -> tuple[SearchIndex, SearchIndex]:
        version = self._version(corpus_version_id)
        self._validate_provider_snapshot(version.embedding_configuration)
        if version.is_frozen:
            lexical = self._ready_index(corpus_version_id, IndexType.LEXICAL)
            dense = self._ready_index(corpus_version_id, IndexType.DENSE)
            return lexical, dense
        if version.status not in {
            CorpusVersionStatus.DRAFT,
            CorpusVersionStatus.INDEXING,
            CorpusVersionStatus.FAILED,
            CorpusVersionStatus.READY,
        }:
            raise CorpusVersionImmutable("archived corpus versions cannot be indexed")

        if not self._chunks(corpus_version_id):
            version.status = CorpusVersionStatus.FAILED
            self.session.flush()
            raise IndexIntegrityError("cannot build a ready index without chunks")

        version.status = CorpusVersionStatus.INDEXING
        try:
            lexical = self.build_lexical(corpus_version_id)
            dense = self.build_dense(corpus_version_id)
            reports = (self.verify(lexical.id), self.verify(dense.id))
            if not all(report.valid for report in reports):
                raise IndexIntegrityError(
                    "one or more indexes failed integrity verification"
                )
            version.status = CorpusVersionStatus.READY
        except Exception:
            version.status = CorpusVersionStatus.FAILED
            self.session.flush()
            raise
        self.session.flush()
        return lexical, dense

    def build_lexical(self, corpus_version_id: UUID) -> SearchIndex:
        version = self._version(corpus_version_id)
        configuration = {
            **lexical_configuration(),
            "chunker": dict(version.chunker_configuration),
        }
        search_index = self._get_or_create_index(
            corpus_version_id, IndexType.LEXICAL, configuration
        )
        chunks = self._chunks(corpus_version_id)
        if search_index.status == IndexStatus.READY and self.verify(search_index.id).valid:
            return search_index
        self._start(search_index, len(chunks))
        try:
            existing = self._existing_chunk_ids(search_index.id)
            for chunk in chunks:
                if chunk.id in existing:
                    continue
                self.session.add(
                    IndexEntry(
                        index_id=search_index.id,
                        chunk_id=chunk.id,
                        searchable_text=chunk.text,
                        embedding=None,
                        entry_metadata=self._entry_metadata(chunk),
                    )
                )
            self.session.flush()
            self._complete(search_index)
            return search_index
        except Exception as exc:
            self._fail(search_index, exc)
            raise

    def build_dense(self, corpus_version_id: UUID) -> SearchIndex:
        version = self._version(corpus_version_id)
        provider = self.embedding_provider
        configuration = {
            **dense_configuration(
                provider.provider_id,
                provider.model_id,
                provider.dimension,
                provider.preprocessing_version,
            ),
            "chunker": dict(version.chunker_configuration),
        }
        search_index = self._get_or_create_index(
            corpus_version_id,
            IndexType.DENSE,
            configuration,
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            embedding_dimension=provider.dimension,
            preprocessing_version=provider.preprocessing_version,
            similarity_method="cosine",
        )
        chunks = self._chunks(corpus_version_id)
        if search_index.status == IndexStatus.READY and self.verify(search_index.id).valid:
            return search_index
        self._start(search_index, len(chunks))
        try:
            existing = self._existing_chunk_ids(search_index.id)
            missing = [chunk for chunk in chunks if chunk.id not in existing]
            for offset in range(0, len(missing), self.batch_size):
                batch = missing[offset : offset + self.batch_size]
                vectors = provider.embed([chunk.text for chunk in batch])
                if len(vectors) != len(batch):
                    raise ValueError("embedding provider returned an invalid batch size")
                for chunk, vector in zip(batch, vectors, strict=True):
                    if len(vector) != provider.dimension:
                        raise ValueError("embedding provider returned an invalid dimension")
                    self.session.add(
                        IndexEntry(
                            index_id=search_index.id,
                            chunk_id=chunk.id,
                            searchable_text=None,
                            embedding=list(vector),
                            entry_metadata=self._entry_metadata(chunk),
                        )
                    )
                self.session.flush()
                search_index.indexed_count = len(existing) + min(
                    offset + len(batch), len(missing)
                )
            self._complete(search_index)
            return search_index
        except Exception as exc:
            self._fail(search_index, exc)
            raise

    def verify(self, index_id: UUID) -> IndexIntegrityReport:
        search_index = self.session.get(SearchIndex, index_id)
        if search_index is None:
            raise IndexNotReady(f"index does not exist: {index_id}")
        expected_ids = {chunk.id for chunk in self._chunks(search_index.corpus_version_id)}
        rows = self.session.execute(
            select(IndexEntry, Chunk).join(Chunk, Chunk.id == IndexEntry.chunk_id).where(
                IndexEntry.index_id == index_id
            )
        ).all()
        indexed_ids: set[UUID] = set()
        foreign = 0
        invalid = 0
        for entry, chunk in rows:
            indexed_ids.add(chunk.id)
            if chunk.corpus_version_id != search_index.corpus_version_id:
                foreign += 1
            if search_index.index_type == IndexType.LEXICAL and entry.searchable_text is None:
                invalid += 1
            if search_index.index_type == IndexType.DENSE:
                if (
                    entry.embedding is None
                    or len(entry.embedding) != search_index.embedding_dimension
                ):
                    invalid += 1
        missing = len(expected_ids - indexed_ids)
        extra = len(indexed_ids - expected_ids)
        errors: list[str] = []
        if foreign:
            errors.append("index contains chunks from another corpus version")
        if missing:
            errors.append("index is missing target corpus-version chunks")
        if extra:
            errors.append("index contains chunks outside the active chunker snapshot")
        if invalid:
            errors.append("index contains entries invalid for its index type/configuration")
        if search_index.failure_count:
            errors.append("index reports embedding/indexing failures")
        valid = not errors and len(rows) == len(expected_ids)
        return IndexIntegrityReport(
            index_id=str(index_id),
            corpus_version_id=str(search_index.corpus_version_id),
            valid=valid,
            expected_chunk_count=len(expected_ids),
            indexed_chunk_count=len(rows),
            failure_count=search_index.failure_count,
            foreign_chunk_count=foreign,
            missing_chunk_count=missing,
            invalid_entry_count=invalid,
            errors=tuple(errors),
        )

    def lexical_search(
        self,
        corpus_version_id: UUID,
        query: str,
        *,
        top_k: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[RetrievalResult]:
        self._require_queryable(corpus_version_id)
        search_index = self._ready_index(corpus_version_id, IndexType.LEXICAL)
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            return self._postgresql_lexical_search(search_index, query, top_k, filters)
        return portable_lexical_search(
            query, self._records(search_index), top_k=top_k, filters=filters
        )

    def dense_search(
        self,
        corpus_version_id: UUID,
        query: str,
        *,
        top_k: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[RetrievalResult]:
        self._require_queryable(corpus_version_id)
        search_index = self._ready_index(corpus_version_id, IndexType.DENSE)
        if (
            search_index.provider_id != self.embedding_provider.provider_id
            or search_index.model_id != self.embedding_provider.model_id
            or search_index.embedding_dimension != self.embedding_provider.dimension
            or search_index.preprocessing_version
            != self.embedding_provider.preprocessing_version
        ):
            raise IndexConfigurationMismatch(
                "query provider does not match the persisted dense-index configuration"
            )
        try:
            vectors = self.embedding_provider.embed([query])
            if len(vectors) != 1 or len(vectors[0]) != self.embedding_provider.dimension:
                raise ValueError("embedding provider returned an invalid query vector")
            query_embedding = vectors[0]
        except Exception as exc:
            raise EmbeddingProviderFailure("query embedding provider failed") from exc
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            return self._postgresql_dense_search(
                search_index, query_embedding, top_k, filters
            )
        # SQLite stores JSON and uses the same cosine definition in-process.
        return portable_dense_search(
            query_embedding, self._records(search_index), top_k=top_k, filters=filters
        )

    def status(self, corpus_version_id: UUID) -> list[IndexIntegrityReport]:
        indexes = self.session.scalars(
            select(SearchIndex).where(SearchIndex.corpus_version_id == corpus_version_id)
        ).all()
        return [self.verify(search_index.id) for search_index in indexes]

    def _version(self, corpus_version_id: UUID) -> CorpusVersion:
        version = self.session.get(CorpusVersion, corpus_version_id)
        if version is None:
            raise CorpusVersionNotFound(f"corpus version does not exist: {corpus_version_id}")
        return version

    def _require_queryable(self, corpus_version_id: UUID) -> CorpusVersion:
        version = self._version(corpus_version_id)
        if version.status != CorpusVersionStatus.READY:
            raise IndexNotReady("corpus version is not ready for retrieval")
        return version

    def _chunks(self, corpus_version_id: UUID) -> list[Chunk]:
        version = self._version(corpus_version_id)
        active_chunker_id = version.chunker_configuration.get("chunker_id")
        statement = select(Chunk).where(Chunk.corpus_version_id == corpus_version_id)
        if active_chunker_id:
            # Several chunker outputs may coexist for comparison, but a search
            # index always represents exactly one active version snapshot.
            statement = statement.where(Chunk.chunker_id == str(active_chunker_id))
        return list(
            self.session.scalars(
                statement.order_by(Chunk.document_id, Chunk.sequence_number, Chunk.id)
            )
        )

    def _get_or_create_index(
        self,
        corpus_version_id: UUID,
        index_type: IndexType,
        configuration: Mapping[str, Any],
        **metadata: Any,
    ) -> SearchIndex:
        configuration_hash = canonical_configuration_hash(configuration)
        search_index = self.session.scalar(
            select(SearchIndex).where(
                SearchIndex.corpus_version_id == corpus_version_id,
                SearchIndex.index_type == index_type,
                SearchIndex.configuration_hash == configuration_hash,
            )
        )
        if search_index is None:
            search_index = SearchIndex(
                corpus_version_id=corpus_version_id,
                index_type=index_type,
                configuration=dict(configuration),
                configuration_hash=configuration_hash,
                **metadata,
            )
            self.session.add(search_index)
            self.session.flush()
        return search_index

    def _ready_index(self, corpus_version_id: UUID, index_type: IndexType) -> SearchIndex:
        search_index = self.session.scalar(
            select(SearchIndex)
            .where(
                SearchIndex.corpus_version_id == corpus_version_id,
                SearchIndex.index_type == index_type,
                SearchIndex.status == IndexStatus.READY,
            )
            .order_by(SearchIndex.completed_at.desc(), SearchIndex.id)
        )
        if search_index is None:
            raise IndexNotReady(f"{index_type.value} index is not ready")
        report = self.verify(search_index.id)
        if not report.valid:
            raise IndexIntegrityError("ready index failed integrity verification")
        return search_index

    def _start(self, search_index: SearchIndex, chunk_count: int) -> None:
        search_index.status = IndexStatus.BUILDING
        search_index.chunk_count = chunk_count
        search_index.failure_count = 0
        search_index.error_code = None
        search_index.error_message = None
        search_index.completed_at = None
        search_index.indexed_count = len(self._existing_chunk_ids(search_index.id))
        self.session.flush()

    def _complete(self, search_index: SearchIndex) -> None:
        search_index.indexed_count = self.session.scalar(
            select(func.count(IndexEntry.id)).where(IndexEntry.index_id == search_index.id)
        ) or 0
        search_index.status = IndexStatus.READY
        search_index.completed_at = _utcnow()
        self.session.flush()

    def _fail(self, search_index: SearchIndex, exc: Exception) -> None:
        search_index.status = IndexStatus.FAILED
        search_index.failure_count = max(
            1, search_index.chunk_count - search_index.indexed_count
        )
        search_index.error_code = "INDEX_BUILD_FAILED"
        search_index.error_message = str(exc)[:2000]
        self.session.flush()

    def _existing_chunk_ids(self, index_id: UUID) -> set[UUID]:
        return set(
            self.session.scalars(
                select(IndexEntry.chunk_id).where(IndexEntry.index_id == index_id)
            )
        )

    @staticmethod
    def _entry_metadata(chunk: Chunk) -> dict[str, Any]:
        return {
            "document_id": str(chunk.document_id),
            "section_path": list(chunk.section_path),
            **dict(chunk.chunk_metadata),
        }

    def _records(self, search_index: SearchIndex) -> list[SearchRecord]:
        # The chunk-version predicate is defense in depth in addition to verify().
        rows = self.session.execute(
            select(IndexEntry, Chunk)
            .join(Chunk, Chunk.id == IndexEntry.chunk_id)
            .where(
                IndexEntry.index_id == search_index.id,
                Chunk.corpus_version_id == search_index.corpus_version_id,
            )
        ).all()
        return [
            SearchRecord(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                corpus_version_id=str(chunk.corpus_version_id),
                text=entry.searchable_text if entry.searchable_text is not None else chunk.text,
                metadata=entry.entry_metadata,
                embedding=entry.embedding,
            )
            for entry, chunk in rows
        ]

    def _postgresql_lexical_search(
        self,
        search_index: SearchIndex,
        query_text: str,
        top_k: int,
        filters: SearchFilters | None,
    ) -> list[RetrievalResult]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        tsquery = func.plainto_tsquery("english", query_text)
        vector = func.to_tsvector("english", IndexEntry.searchable_text)
        rank_expression = func.ts_rank_cd(vector, tsquery).label("score")
        statement: Select[Any] = (
            select(IndexEntry, Chunk, rank_expression)
            .join(Chunk, Chunk.id == IndexEntry.chunk_id)
            .where(
                IndexEntry.index_id == search_index.id,
                Chunk.corpus_version_id == search_index.corpus_version_id,
                vector.op("@@")(tsquery),
            )
            .order_by(rank_expression.desc(), Chunk.id)
        )
        if filters and filters.document_ids:
            statement = statement.where(
                Chunk.document_id.in_([UUID(value) for value in filters.document_ids])
            )
        # Arbitrary JSON equality filters are applied after FTS ranking for a small
        # Chunk 1 contract. Fetch extra candidates so filtering does not trivially
        # under-fill top-k; a future typed metadata schema can push these to SQL.
        rows = self.session.execute(statement.limit(top_k * 10)).all()
        results: list[RetrievalResult] = []
        for entry, chunk, score in rows:
            record = SearchRecord(
                str(chunk.id),
                str(chunk.document_id),
                str(chunk.corpus_version_id),
                entry.searchable_text or "",
                entry.entry_metadata,
            )
            if filters and any(
                record.metadata.get(key) != value
                for key, value in filters.metadata_equals.items()
            ):
                continue
            results.append(
                RetrievalResult(
                    chunk_id=record.chunk_id,
                    document_id=record.document_id,
                    corpus_version_id=record.corpus_version_id,
                    text=record.text,
                    rank=len(results) + 1,
                    score=float(score),
                    retrieval_method="postgresql_fts_ts_rank_cd",
                    metadata=record.metadata,
                )
            )
            if len(results) == top_k:
                break
        return results

    def _postgresql_dense_search(
        self,
        search_index: SearchIndex,
        query_embedding: list[float],
        top_k: int,
        filters: SearchFilters | None,
    ) -> list[RetrievalResult]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        dimension = search_index.embedding_dimension
        if dimension is None:
            raise IndexConfigurationMismatch("dense index has no embedding dimension")
        # The model maps JSON on SQLite and native `vector` on PostgreSQL. Casting
        # here gives SQLAlchemy pgvector's comparator even though the portable
        # column's base comparator is JSON.
        vector_column = cast(IndexEntry.embedding, Vector(dimension))
        query_vector = literal(query_embedding, type_=Vector(dimension))
        distance = vector_column.cosine_distance(query_vector).label("distance")
        statement: Select[Any] = (
            select(IndexEntry, Chunk, distance)
            .join(Chunk, Chunk.id == IndexEntry.chunk_id)
            .where(
                IndexEntry.index_id == search_index.id,
                Chunk.corpus_version_id == search_index.corpus_version_id,
                IndexEntry.embedding.is_not(None),
            )
            .order_by(distance.asc(), Chunk.id)
        )
        if filters and filters.document_ids:
            statement = statement.where(
                Chunk.document_id.in_([UUID(value) for value in filters.document_ids])
            )
        rows = self.session.execute(statement.limit(top_k * 10)).all()
        results: list[RetrievalResult] = []
        for entry, chunk, raw_distance in rows:
            metadata = entry.entry_metadata
            if filters and any(
                metadata.get(key) != value
                for key, value in filters.metadata_equals.items()
            ):
                continue
            results.append(
                RetrievalResult(
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    corpus_version_id=str(chunk.corpus_version_id),
                    text=chunk.text,
                    rank=len(results) + 1,
                    score=1.0 - float(raw_distance),
                    retrieval_method="postgresql_pgvector_cosine",
                    metadata=metadata,
                )
            )
            if len(results) == top_k:
                break
        return results

    def _validate_provider_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        expected = {
            "provider": self.embedding_provider.provider_id,
            "model": self.embedding_provider.model_id,
            "dimension": self.embedding_provider.dimension,
            "preprocessing_version": self.embedding_provider.preprocessing_version,
        }
        conflicts = {
            key: (snapshot[key], value)
            for key, value in expected.items()
            if key in snapshot and snapshot[key] != value
        }
        if conflicts:
            raise IndexConfigurationMismatch(
                f"embedding provider conflicts with corpus-version snapshot: {conflicts}"
            )
