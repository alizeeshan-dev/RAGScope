"""Document ingestion, parsing, artifact inspection, and chunk endpoints."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings, get_settings
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import Artifact, Chunk, CorpusVersion, DocumentElement, SourceDocument
from backend.app.db.session import get_db
from backend.app.documents.chunkers import (
    Chunker,
    FixedTokenChunker,
    FixedTokenConfiguration,
    StructureAwareChunker,
    StructureAwareConfiguration,
)
from backend.app.documents.service import DocumentService
from backend.app.indexing.api_schemas import OperationAccepted
from backend.app.jobs.api_contract import receipt, run_inline_for_bounded_wait
from backend.app.jobs.service import create_job

router = APIRouter(tags=["documents"])


@lru_cache
def _artifact_store(root: str) -> LocalArtifactStore:
    return LocalArtifactStore(root)


def get_artifact_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LocalArtifactStore:
    return _artifact_store(str(settings.artifact_root))


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_version_id: UUID
    title: str | None
    authors: list[str]
    publication_year: int | None
    source_type: str | None
    source_uri: str | None
    license_information: str | None
    file_hash: str
    mime_type: str
    page_count: int | None
    parse_status: str
    parse_warnings: list[dict[str, object]]
    metadata_provenance: dict[str, object]
    created_at: datetime


class DocumentMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    authors: list[str] | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=3000)
    source_uri: str | None = None
    license_information: str | None = None


class ElementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    parent_element_id: UUID | None
    element_type: str
    sequence_number: int
    page_number: int | None
    section_path: list[str]
    text: str
    bounding_box: dict[str, object] | None
    parser_metadata: dict[str, object]


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artifact_type: str
    content_hash: str
    media_type: str
    original_filename: str | None
    producing_operation: str
    producer_version: str | None
    configuration: dict[str, object]
    size_bytes: int


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    corpus_version_id: UUID
    chunker_id: str
    sequence_number: int
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: list[str]
    source_element_ids: list[str]
    content_hash: str
    metadata: dict[str, object] = Field(validation_alias="chunk_metadata")


class ChunkRequest(BaseModel):
    strategy: Literal["fixed", "structure-aware"] = "structure-aware"
    target_tokens: int = Field(default=384, ge=1, le=100_000)
    overlap_tokens: int = Field(default=32, ge=0)
    include_section_titles: bool = True
    preserve_tables: bool = True


@router.post(
    "/corpus-versions/{version_id}/documents",
    response_model=DocumentRead,
    status_code=201,
)
async def upload_document(
    version_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
) -> SourceDocument:
    # Reading max+1 bounds request memory and lets validation return stable FILE_TOO_LARGE.
    content = await file.read(settings.max_upload_bytes + 1)
    service = DocumentService(session, artifact_store, max_upload_bytes=settings.max_upload_bytes)
    try:
        document = service.upload(
            corpus_version_id=version_id,
            filename=file.filename or "",
            content=content,
            claimed_media_type=file.content_type,
            title=title,
        )
        session.commit()
        session.refresh(document)
        return document
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> SourceDocument:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/corpus-versions/{version_id}/documents", response_model=list[DocumentRead])
def list_documents(
    version_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SourceDocument]:
    if session.get(CorpusVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="Corpus version not found")
    return list(
        session.scalars(
            select(SourceDocument)
            .where(SourceDocument.corpus_version_id == version_id)
            .order_by(SourceDocument.created_at, SourceDocument.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> None:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    from backend.app.documents.service import assert_version_editable

    assert_version_editable(document.corpus_version)
    # Artifact bytes are immutable. Draft removal drops database references; a
    # maintenance command may safely reclaim unreferenced files later.
    session.execute(delete(Artifact).where(Artifact.document_id == document.id))
    version = document.corpus_version
    version.document_count = max(0, version.document_count - 1)
    session.delete(document)
    session.commit()


@router.patch("/documents/{document_id}", response_model=DocumentRead)
def update_document_metadata(
    document_id: UUID,
    update: DocumentMetadataUpdate,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
) -> SourceDocument:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    service = DocumentService(session, artifact_store, max_upload_bytes=settings.max_upload_bytes)
    document = service.update_metadata(document, update.model_dump(exclude_unset=True))
    session.commit()
    session.refresh(document)
    return document


@router.get("/documents/{document_id}/elements", response_model=list[ElementRead])
def get_document_elements(
    document_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> list[DocumentElement]:
    if session.get(SourceDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return list(
        session.scalars(
            select(DocumentElement)
            .where(DocumentElement.document_id == document_id)
            .order_by(DocumentElement.sequence_number)
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.post(
    "/documents/{document_id}/parse",
    response_model=DocumentRead | OperationAccepted,
)
def parse_document(
    document_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> SourceDocument | OperationAccepted:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    parse_snapshot = dict(document.corpus_version.parser_configuration)
    snapshot_hash = hashlib.sha256(canonical_json(parse_snapshot)).hexdigest()
    job, created = create_job(
        session,
        job_type="document_parsing",
        input_reference={"document_id": str(document.id), "configuration": parse_snapshot},
        idempotency_key=f"parse:{document.id}:{document.file_hash}:{snapshot_hash}",
        progress_total=1,
    )
    if not created and job.status.value == "succeeded" and document.parse_status.value == "ready":
        return document
    session.commit()
    if not (settings.job_api_default_wait if wait is None else wait):
        response.status_code = 202
        return receipt(job, resource_type="document", resource_id=document.id)
    completed = run_inline_for_bounded_wait(
        session,
        job,
        settings=settings,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
    )
    if not completed:
        response.status_code = 202
        return receipt(job, resource_type="document", resource_id=document.id)
    session.refresh(document)
    return document


@router.get("/documents/{document_id}/artifacts", response_model=list[ArtifactRead])
def get_document_artifacts(
    document_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> list[Artifact]:
    if session.get(SourceDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return list(
        session.scalars(
            select(Artifact)
            .where(Artifact.document_id == document_id)
            .order_by(Artifact.created_at)
        ).all()
    )


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
) -> StreamingResponse:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    def body() -> Iterator[bytes]:
        with artifact_store.open(artifact.storage_key) as stream:
            while data := stream.read(64 * 1024):
                yield data

    headers = {"X-Content-SHA256": artifact.content_hash}
    if artifact.original_filename:
        # Filename has already been sanitized; quote characters are removed by the
        # sanitizer, but replace once more at the response boundary.
        display_name = artifact.original_filename.replace('"', "_")
        headers["Content-Disposition"] = f'inline; filename="{display_name}"'
    return StreamingResponse(body(), media_type=artifact.media_type, headers=headers)


@router.post(
    "/corpus-versions/{version_id}/chunk",
    response_model=list[ChunkRead] | OperationAccepted,
)
def generate_chunks(
    version_id: UUID,
    request: ChunkRequest,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
    wait: bool | None = None,
    timeout_seconds: Annotated[int, Query(ge=1, le=60)] = 30,
) -> list[Chunk] | OperationAccepted:
    version = session.get(CorpusVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Corpus version not found")
    chunker: Chunker
    if request.strategy == "fixed":
        try:
            configuration = FixedTokenConfiguration(
                target_tokens=request.target_tokens,
                overlap_tokens=request.overlap_tokens,
                include_section_titles=request.include_section_titles,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        chunker = FixedTokenChunker(configuration)
        configuration_snapshot = configuration.as_dict()
    else:
        structure_configuration = StructureAwareConfiguration(
            target_tokens=request.target_tokens,
            include_section_titles=request.include_section_titles,
            preserve_tables=request.preserve_tables,
        )
        chunker = StructureAwareChunker(structure_configuration)
        configuration_snapshot = structure_configuration.as_dict()
    version_snapshot = {
        "chunker_id": chunker.chunker_id,
        "configuration": configuration_snapshot,
    }
    snapshot_hash = hashlib.sha256(canonical_json(version_snapshot)).hexdigest()
    job, created = create_job(
        session,
        job_type="chunk_generation",
        input_reference={
            "corpus_version_id": str(version.id),
            "strategy": request.strategy,
            **version_snapshot,
        },
        idempotency_key=f"chunk:{version.id}:{snapshot_hash}",
        progress_total=version.document_count,
    )
    if not created and job.status.value == "succeeded":
        version.chunker_configuration = version_snapshot
        existing_chunks = list(
            session.scalars(
                select(Chunk)
                .where(
                    Chunk.corpus_version_id == version.id,
                    Chunk.chunker_id == chunker.chunker_id,
                )
                .order_by(Chunk.document_id, Chunk.sequence_number)
            )
        )
        session.commit()
        return existing_chunks
    session.commit()
    if not (settings.job_api_default_wait if wait is None else wait):
        response.status_code = 202
        return receipt(job, resource_type="corpus_version", resource_id=version.id)
    completed = run_inline_for_bounded_wait(
        session,
        job,
        settings=settings,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
    )
    if not completed:
        response.status_code = 202
        return receipt(job, resource_type="corpus_version", resource_id=version.id)
    chunks = list(
        session.scalars(
            select(Chunk)
            .where(
                Chunk.corpus_version_id == version.id,
                Chunk.chunker_id == chunker.chunker_id,
            )
            .order_by(Chunk.document_id, Chunk.sequence_number)
        )
    )
    for chunk in chunks:
        session.refresh(chunk)
    return chunks


@router.get("/corpus-versions/{version_id}/chunks", response_model=list[ChunkRead])
def get_chunks(
    version_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    document_id: UUID | None = None,
    chunker_id: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> list[Chunk]:
    if session.get(CorpusVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="Corpus version not found")
    query = select(Chunk).where(Chunk.corpus_version_id == version_id)
    if document_id is not None:
        query = query.where(Chunk.document_id == document_id)
    if chunker_id is not None:
        query = query.where(Chunk.chunker_id == chunker_id)
    return list(
        session.scalars(
            query.order_by(Chunk.document_id, Chunk.chunker_id, Chunk.sequence_number)
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.get("/chunks/{chunk_id}", response_model=ChunkRead)
def get_chunk(
    chunk_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> Chunk:
    chunk = session.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk
