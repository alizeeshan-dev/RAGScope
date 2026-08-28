from __future__ import annotations

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.routes.documents import get_artifact_store
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.db.models import SearchIndex
from backend.app.db.session import get_db
from backend.app.indexing.api_schemas import (
    IndexStatusRead,
    OperationAccepted,
    SearchRequest,
    SearchResultRead,
)
from backend.app.indexing.errors import IndexingError
from backend.app.indexing.schemas import SearchFilters
from backend.app.indexing.service import IndexingService
from backend.app.jobs.api_contract import receipt, run_inline_for_bounded_wait
from backend.app.jobs.service import create_job
from backend.app.providers.registry import create_embedding_provider

router = APIRouter(tags=["indexing"])
SessionDependency = Annotated[Session, Depends(get_db)]


def _service(session: Session, version_id: UUID) -> IndexingService:
    from backend.app.corpora.service import get_version_or_error

    version = get_version_or_error(session, version_id)
    provider = create_embedding_provider(version.embedding_configuration)
    return IndexingService(session, provider)


def _domain_error(exc: IndexingError) -> DomainError:
    status_code = 404 if exc.code == "CORPUS_VERSION_NOT_FOUND" else 409
    return DomainError(exc.code, str(exc), status_code=status_code)


@router.post(
    "/corpus-versions/{version_id}/index",
    response_model=OperationAccepted,
    status_code=202,
)
def build_indexes(
    version_id: UUID,
    response: Response,
    session: SessionDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> OperationAccepted:
    from backend.app.corpora.service import get_version_or_error

    version = get_version_or_error(session, version_id)
    job, _created = create_job(
        session,
        job_type="build_indexes",
        input_reference={
            "corpus_version_id": str(version.id),
            "embedding_configuration": version.embedding_configuration,
        },
        idempotency_key=f"index:{version.id}:{version.content_hash or 'draft'}",
        progress_total=2,
    )
    session.commit()
    if settings.job_api_default_wait if wait is None else wait:
        completed = run_inline_for_bounded_wait(
            session,
            job,
            settings=settings,
            artifact_store=artifact_store,
            timeout_seconds=timeout_seconds,
        )
        response.status_code = 200 if completed else 202
    return receipt(job, resource_type="corpus_version", resource_id=version.id)


@router.get(
    "/corpus-versions/{version_id}/index-status", response_model=list[IndexStatusRead]
)
def index_status(version_id: UUID, session: SessionDependency) -> list[IndexStatusRead]:
    service = _service(session, version_id)
    indexes = session.scalars(
        select(SearchIndex)
        .where(SearchIndex.corpus_version_id == version_id)
        .order_by(SearchIndex.index_type, SearchIndex.created_at)
    ).all()
    return [
        IndexStatusRead(
            id=index.id,
            index_type=index.index_type,
            status=index.status,
            configuration=index.configuration,
            provider_id=index.provider_id,
            model_id=index.model_id,
            embedding_dimension=index.embedding_dimension,
            preprocessing_version=index.preprocessing_version,
            similarity_method=index.similarity_method,
            chunk_count=index.chunk_count,
            indexed_count=index.indexed_count,
            failure_count=index.failure_count,
            integrity_valid=report.valid,
            integrity_errors=list(report.errors),
        )
        for index in indexes
        for report in [service.verify(index.id)]
    ]


def _filters(payload: SearchRequest) -> SearchFilters:
    return SearchFilters(
        document_ids=frozenset(str(identifier) for identifier in payload.document_ids),
        metadata_equals=payload.metadata_equals,
    )


@router.post(
    "/corpus-versions/{version_id}/search/lexical", response_model=list[SearchResultRead]
)
def lexical_search(
    version_id: UUID, payload: SearchRequest, session: SessionDependency
) -> list[SearchResultRead]:
    try:
        results = _service(session, version_id).lexical_search(
            version_id, payload.query, top_k=payload.top_k, filters=_filters(payload)
        )
    except IndexingError as exc:
        raise _domain_error(exc) from exc
    return [SearchResultRead(**asdict(result)) for result in results]


@router.post(
    "/corpus-versions/{version_id}/search/dense", response_model=list[SearchResultRead]
)
def dense_search(
    version_id: UUID, payload: SearchRequest, session: SessionDependency
) -> list[SearchResultRead]:
    try:
        results = _service(session, version_id).dense_search(
            version_id, payload.query, top_k=payload.top_k, filters=_filters(payload)
        )
    except IndexingError as exc:
        raise _domain_error(exc) from exc
    return [SearchResultRead(**asdict(result)) for result in results]
