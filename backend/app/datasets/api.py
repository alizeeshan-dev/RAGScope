from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings, get_settings
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import DatasetRecord, JobStatus, SourceDocument
from backend.app.db.session import get_db
from backend.app.jobs.service import complete_job, create_job, fail_job, start_job
from backend.app.providers.base import GenerationProvider
from backend.app.providers.registry import create_generation_provider

from .errors import DatasetExtractionError
from .schemas import (
    DatasetComparisonRead,
    DatasetComparisonRequest,
    DatasetExtractionRequest,
    DatasetExtractionRunRead,
    DatasetRecordPatch,
    DatasetRecordRead,
    DatasetReviewRequest,
    FieldEvidenceRead,
    HumanFieldEvidenceCreate,
)
from .service import (
    DatasetCatalogService,
    DatasetEvidenceService,
    DatasetExtractionService,
    DatasetReviewService,
)
from .strategies import DeterministicDatasetGenerationProvider

router = APIRouter(tags=["datasets"])


@lru_cache
def _artifact_store(root: str) -> LocalArtifactStore:
    return LocalArtifactStore(root)


def get_dataset_artifact_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LocalArtifactStore:
    return _artifact_store(str(settings.artifact_root))


def _provider(request: DatasetExtractionRequest, settings: Settings) -> GenerationProvider:
    if request.provider == "fake":
        return DeterministicDatasetGenerationProvider()
    configuration: dict[str, Any] = {"provider": request.provider}
    if request.model is not None:
        configuration["model"] = request.model
    return create_generation_provider(configuration, settings=settings)


def _read(record: DatasetRecord) -> DatasetRecordRead:
    _ = record.evidence, record.review_history
    return DatasetRecordRead.model_validate(record)


@router.post(
    "/documents/{document_id}/extract-datasets",
    response_model=DatasetExtractionRunRead,
)
def extract_datasets(
    document_id: UUID,
    request: DatasetExtractionRequest,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    artifact_store: Annotated[LocalArtifactStore, Depends(get_dataset_artifact_store)],
) -> DatasetExtractionRunRead:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise DatasetExtractionError("DOCUMENT_NOT_FOUND", "Document not found", status_code=404)
    snapshot = request.model_dump(mode="json")
    snapshot_hash = hashlib.sha256(canonical_json(snapshot)).hexdigest()
    job, created = create_job(
        session,
        job_type="dataset_extraction",
        input_reference={"document_id": str(document.id), "configuration": snapshot},
        idempotency_key=f"dataset-extraction:{document.id}:{document.file_hash}:{snapshot_hash}",
        progress_total=1,
    )
    if not created and job.status is JobStatus.SUCCEEDED:
        record_ids = list(
            session.scalars(
                select(DatasetRecord.id).where(DatasetRecord.extraction_job_id == job.id)
            )
        )
        return DatasetExtractionRunRead(
            job_id=job.id, record_ids=record_ids, status=job.status.value
        )
    start_job(job)
    try:
        record = DatasetExtractionService(
            session, artifact_store, _provider(request, settings)
        ).extract(document.id, request, job=job)
        artifact_ids = [
            str(value)
            for value in (record.raw_response_artifact_id, record.structured_result_artifact_id)
            if value is not None
        ]
        complete_job(job, result_artifact_ids=artifact_ids)
        session.commit()
        return DatasetExtractionRunRead(
            job_id=job.id, record_ids=[record.id], status=job.status.value
        )
    except Exception as exc:
        fail_job(
            job,
            error_code=getattr(exc, "code", "DATASET_EXTRACTION_FAILED"),
            error_message=str(exc)[:2_000],
        )
        session.commit()
        raise


@router.get("/dataset-records", response_model=list[DatasetRecordRead])
def list_dataset_records(
    session: Annotated[Session, Depends(get_db)],
    corpus_version_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=500)] = None,
    domain: Annotated[str | None, Query(max_length=255)] = None,
    modality: Annotated[str | None, Query(max_length=255)] = None,
    task_type: Annotated[str | None, Query(max_length=255)] = None,
    language: Annotated[str | None, Query(max_length=255)] = None,
    license_name: Annotated[str | None, Query(alias="license", max_length=500)] = None,
    review_status: Annotated[str | None, Query(max_length=50)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[DatasetRecordRead]:
    records = DatasetCatalogService(session).list_records(
        corpus_version_id=corpus_version_id,
        search=search,
        domain=domain,
        modality=modality,
        task_type=task_type,
        language=language,
        license_name=license_name,
        review_status=review_status,
        offset=offset,
        limit=limit,
    )
    return [_read(record) for record in records]


@router.post("/dataset-records/compare", response_model=DatasetComparisonRead)
def compare_dataset_records(
    request: DatasetComparisonRequest,
    session: Annotated[Session, Depends(get_db)],
) -> DatasetComparisonRead:
    corpus_id, comparison = DatasetCatalogService(session).comparison(request.record_ids)
    return DatasetComparisonRead(
        corpus_version_id=corpus_id,
        fields=comparison["fields"],
        records=[_read(record) for record in comparison["records"]],
    )


@router.get("/dataset-records/{record_id}", response_model=DatasetRecordRead)
def get_dataset_record(
    record_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> DatasetRecordRead:
    return _read(DatasetCatalogService(session).get(record_id))


@router.patch("/dataset-records/{record_id}", response_model=DatasetRecordRead)
def patch_dataset_record(
    record_id: UUID,
    request: DatasetRecordPatch,
    session: Annotated[Session, Depends(get_db)],
) -> DatasetRecordRead:
    service = DatasetReviewService(session)
    record: DatasetRecord | None = None
    for field_name, value in request.updates.items():
        action = "edit" if value is not None else "clear"
        record = service.review(
            record_id,
            DatasetReviewRequest(
                action=action,
                field_name=field_name,
                value=value,
                evidence_ids=request.evidence_ids.get(field_name, []),
                reviewer_note=request.reviewer_note,
            ),
        )
    assert record is not None
    session.commit()
    session.refresh(record)
    return _read(record)


@router.post("/dataset-records/{record_id}/review", response_model=DatasetRecordRead)
def review_dataset_record(
    record_id: UUID,
    request: DatasetReviewRequest,
    session: Annotated[Session, Depends(get_db)],
) -> DatasetRecordRead:
    record = DatasetReviewService(session).review(record_id, request)
    session.commit()
    session.refresh(record)
    return _read(record)


@router.post(
    "/dataset-records/{record_id}/evidence",
    response_model=FieldEvidenceRead,
    status_code=201,
)
def add_dataset_field_evidence(
    record_id: UUID,
    request: HumanFieldEvidenceCreate,
    session: Annotated[Session, Depends(get_db)],
) -> FieldEvidenceRead:
    evidence = DatasetEvidenceService(session).add(record_id, request)
    session.commit()
    session.refresh(evidence)
    return FieldEvidenceRead.model_validate(evidence)


@router.get("/dataset-records/{record_id}/export")
def export_dataset_record(
    record_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> Response:
    payload = DatasetCatalogService(session).export(record_id)
    return Response(
        content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="dataset-record-{record_id}.json"'},
    )
