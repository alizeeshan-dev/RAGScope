from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.db.models import (
    Artifact,
    Chunk,
    DatasetExtractionStatus,
    DatasetFieldReviewRevision,
    DatasetRecord,
    DatasetReviewStatus,
    DocumentElement,
    FieldEvidence,
    FieldReviewStatus,
    Job,
    ParseStatus,
    SourceDocument,
)
from backend.app.providers.base import GenerationProvider, StructuredGenerationResult
from backend.app.tracing.redaction import configured_sensitive_values, redact

from .errors import (
    DatasetExtractionError,
    DatasetNotFoundError,
    DatasetReviewError,
    InvalidDatasetEvidenceError,
    InvalidDatasetOutputError,
)
from .schemas import (
    DATASET_FIELDS,
    INTEGER_FIELDS,
    LIST_FIELDS,
    DatasetExtractionOutput,
    DatasetExtractionRequest,
    DatasetReviewRequest,
    ExtractionStrategy,
    HumanFieldEvidenceCreate,
    ValueState,
)
from .strategies import SourceChunk, extraction_prompts, select_sources


def _json_compatible(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class DatasetExtractionService:
    """Generate one strict record and resolve its evidence to source provenance."""

    def __init__(
        self,
        session: Session,
        artifact_store: LocalArtifactStore,
        provider: GenerationProvider,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.provider = provider

    def extract(
        self,
        document_id: UUID,
        request: DatasetExtractionRequest,
        *,
        job: Job,
    ) -> DatasetRecord:
        document = self.session.get(SourceDocument, document_id)
        if document is None:
            raise DatasetExtractionError(
                "DOCUMENT_NOT_FOUND", "Document not found", status_code=404
            )
        if document.parse_status is not ParseStatus.READY:
            raise DatasetExtractionError(
                "DOCUMENT_NOT_READY",
                "Document must be parsed before dataset extraction",
                status_code=409,
            )
        source_chunks = self._source_chunks(document)
        if not source_chunks:
            raise DatasetExtractionError(
                "DOCUMENT_NOT_READY",
                "Document has no provenance-preserving chunks",
                status_code=409,
            )
        candidates = select_sources(
            source_chunks,
            strategy=request.strategy,
            candidate_count=request.retrieval_candidate_count,
            maximum_source_chunks=request.maximum_source_chunks,
        )
        system_prompt, user_prompt = extraction_prompts(
            document_title=document.title,
            candidates=candidates,
            strategy=request.strategy,
        )
        configuration = self._configuration(request, system_prompt, user_prompt)
        try:
            response = self.provider.generate_structured(
                user_prompt,
                system_prompt=system_prompt,
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
                response_schema=DatasetExtractionOutput.model_json_schema(),
            )
        except Exception as exc:
            raise DatasetExtractionError(
                "DATASET_EXTRACTION_PROVIDER_FAILURE",
                f"Dataset extraction provider failed: {type(exc).__name__}",
                status_code=502,
            ) from exc

        raw_artifact = self._persist_raw_response(document, job, response, configuration)
        try:
            output = DatasetExtractionOutput.model_validate_json(response.content)
        except ValidationError as exc:
            raise InvalidDatasetOutputError(str(exc)[:4_000]) from exc
        evidence_by_id = {candidate.evidence_id: candidate for candidate in candidates}
        self._validate_evidence(output, evidence_by_id)
        structured_artifact = self._persist_structured_result(document, job, output, configuration)

        original_values = {
            field_name: getattr(output, field_name).value for field_name in DATASET_FIELDS
        }
        states = {
            field_name: {
                "extraction_state": getattr(output, field_name).state.value,
                "review_status": "unreviewed",
                "evidence_backed": bool(getattr(output, field_name).evidence_ids),
                "value_source": "model",
            }
            for field_name in DATASET_FIELDS
        }
        record = DatasetRecord(
            corpus_version_id=document.corpus_version_id,
            source_document_id=document.id,
            extraction_job_id=job.id,
            strategy=request.strategy.value,
            original_values=_json_compatible(original_values),
            current_values=_json_compatible(original_values),
            field_states=states,
            extraction_configuration=configuration,
            extraction_status=DatasetExtractionStatus.VALID,
            review_status=DatasetReviewStatus.UNREVIEWED,
            raw_response_artifact_id=raw_artifact.id,
            structured_result_artifact_id=structured_artifact.id,
        )
        self._sync_catalog_columns(record)
        self.session.add(record)
        self.session.flush()

        for field_name in DATASET_FIELDS:
            extracted = getattr(output, field_name)
            for evidence_id in extracted.evidence_ids:
                candidate = evidence_by_id[evidence_id]
                self.session.add(
                    FieldEvidence(
                        dataset_record_id=record.id,
                        field_name=field_name,
                        document_id=candidate.document_id,
                        page_number=candidate.page_number,
                        element_id=candidate.element_id,
                        chunk_id=candidate.chunk_id,
                        supporting_text=candidate.supporting_text,
                        original_supporting_text=candidate.supporting_text,
                        extraction_method=request.strategy.value,
                        model_confidence_label="not_provided",
                        review_status=FieldReviewStatus.UNREVIEWED,
                        reviewer_note=None,
                    )
                )
        self.session.flush()
        return record

    def _source_chunks(self, document: SourceDocument) -> list[SourceChunk]:
        chunks = list(
            self.session.scalars(
                select(Chunk)
                .where(
                    Chunk.document_id == document.id,
                    Chunk.corpus_version_id == document.corpus_version_id,
                )
                .order_by(Chunk.sequence_number, Chunk.id)
            )
        )
        element_ids: set[UUID] = set()
        first_element_by_chunk: dict[UUID, UUID] = {}
        for chunk in chunks:
            for raw_id in chunk.source_element_ids:
                try:
                    element_id = UUID(raw_id)
                except (TypeError, ValueError):
                    continue
                first_element_by_chunk.setdefault(chunk.id, element_id)
                element_ids.add(element_id)
        valid_elements = (
            {
                element.id: element
                for element in self.session.scalars(
                    select(DocumentElement).where(
                        DocumentElement.document_id == document.id,
                        DocumentElement.id.in_(element_ids),
                    )
                )
            }
            if element_ids
            else {}
        )
        result: list[SourceChunk] = []
        for chunk in chunks:
            candidate_element_id = first_element_by_chunk.get(chunk.id)
            element = valid_elements.get(candidate_element_id) if candidate_element_id else None
            result.append(
                SourceChunk(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    element_id=element.id if element else None,
                    page_number=element.page_number if element else chunk.page_start,
                    text=chunk.text,
                    sequence_number=chunk.sequence_number,
                )
            )
        return result

    def _configuration(
        self,
        request: DatasetExtractionRequest,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        return {
            "strategy": request.strategy.value,
            "prompt_version": request.prompt_version,
            "system_prompt_sha256": sha256(system_prompt.encode()).hexdigest(),
            "provider_prompt_sha256": sha256(user_prompt.encode()).hexdigest(),
            "provider": self.provider.provider_id,
            "model": self.provider.model_id,
            "requested_model": request.model,
            "retrieval": {
                "candidate_count": request.retrieval_candidate_count,
                "maximum_source_chunks": request.maximum_source_chunks,
                "method": "deterministic_field_term_ranking_v1"
                if request.strategy is ExtractionStrategy.RETRIEVAL_ASSISTED
                else "document_order_v1",
            },
            "field_schema_version": request.field_schema_version,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }

    @staticmethod
    def _validate_evidence(
        output: DatasetExtractionOutput, evidence_by_id: Mapping[str, Any]
    ) -> None:
        for field_name in DATASET_FIELDS:
            field = getattr(output, field_name)
            invalid = set(field.evidence_ids) - set(evidence_by_id)
            if invalid:
                raise InvalidDatasetEvidenceError(
                    f"{field_name} references unknown evidence IDs: {sorted(invalid)}"
                )

    def _persist_raw_response(
        self,
        document: SourceDocument,
        job: Job,
        response: StructuredGenerationResult,
        configuration: dict[str, Any],
    ) -> Artifact:
        safe = redact(
            response.raw_response,
            sensitive_values=tuple(configured_sensitive_values()),
        )
        if not isinstance(safe, str):
            raise TypeError("redacted extraction response must remain text")
        return self._persist_artifact(
            document=document,
            job=job,
            content=safe.encode(),
            artifact_type="dataset_extraction_raw_response",
            media_type="application/json",
            producing_operation="dataset_metadata_extraction_raw_response",
            producer_version=response.model_id,
            configuration=configuration,
        )

    def _persist_structured_result(
        self,
        document: SourceDocument,
        job: Job,
        output: DatasetExtractionOutput,
        configuration: dict[str, Any],
    ) -> Artifact:
        return self._persist_artifact(
            document=document,
            job=job,
            content=output.model_dump_json().encode(),
            artifact_type="dataset_extraction_structured_result",
            media_type="application/json",
            producing_operation="dataset_metadata_extraction_structured_result",
            producer_version=str(configuration["field_schema_version"]),
            configuration=configuration,
        )

    def _persist_artifact(
        self,
        *,
        document: SourceDocument,
        job: Job,
        content: bytes,
        artifact_type: str,
        media_type: str,
        producing_operation: str,
        producer_version: str,
        configuration: dict[str, Any],
    ) -> Artifact:
        descriptor = self.artifact_store.put_bytes(
            content,
            media_type=media_type,
            producing_operation=producing_operation,
            configuration=configuration,
        )
        artifact = Artifact(
            id=descriptor.id,
            corpus_version_id=document.corpus_version_id,
            document_id=document.id,
            job_id=job.id,
            query_run_id=None,
            trace_span_id=None,
            artifact_type=artifact_type,
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=None,
            producing_operation=descriptor.producing_operation,
            producer_version=producer_version,
            configuration=configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    @staticmethod
    def _sync_catalog_columns(record: DatasetRecord) -> None:
        for field_name in DATASET_FIELDS:
            value = record.current_values.get(field_name)
            setattr(record, field_name, value)


class DatasetReviewService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def review(self, record_id: UUID, request: DatasetReviewRequest) -> DatasetRecord:
        record = self._record(record_id)
        if request.action == "approve_record":
            self._approve(record)
            self.session.flush()
            return record
        if request.action == "reopen_record":
            record.review_status = DatasetReviewStatus.IN_REVIEW
            self.session.flush()
            return record
        assert request.field_name is not None
        field_name = request.field_name
        previous_value = record.current_values.get(field_name)
        previous_state = self._field_state(record, field_name)
        evidence = self._resolve_evidence(record, field_name, request.evidence_ids)

        if request.action == "accept":
            if (
                previous_value is None
                or previous_state["extraction_state"] == ValueState.NOT_STATED
            ):
                raise DatasetReviewError("A null/not-stated field cannot be accepted as stated")
            evidence = evidence or self._field_evidence(record.id, field_name)
            if not evidence:
                raise DatasetReviewError("A non-null field cannot be accepted without evidence")
            new_value = previous_value
            new_state = ValueState.STATED.value
            status = "accepted"
            value_source = previous_state.get("value_source", "model")
        elif request.action == "edit":
            self._validate_value(field_name, request.value)
            new_value = request.value
            new_state = ValueState.STATED.value
            status = "edited"
            value_source = "human"
        elif request.action == "reject":
            new_value = None
            new_state = "rejected"
            status = "rejected"
            value_source = "human"
        elif request.action == "clear":
            new_value = None
            new_state = "unknown"
            status = "cleared"
            value_source = "human"
        else:
            new_value = None
            new_state = ValueState.NOT_STATED.value
            status = "not_stated"
            value_source = "human"

        current_values = dict(record.current_values)
        current_values[field_name] = _json_compatible(new_value)
        record.current_values = current_values
        field_states = dict(record.field_states)
        field_states[field_name] = {
            "extraction_state": new_state,
            "review_status": status,
            "evidence_backed": bool(evidence) if new_value is not None else False,
            "value_source": value_source,
        }
        record.field_states = field_states
        record.review_status = DatasetReviewStatus.IN_REVIEW
        DatasetExtractionService._sync_catalog_columns(record)
        selected_ids = {item.id for item in evidence}
        for item in self._field_evidence(record.id, field_name):
            if request.action in {"reject", "clear", "mark_not_stated"}:
                item.review_status = FieldReviewStatus(status)
            elif item.id in selected_ids:
                item.review_status = FieldReviewStatus.ACCEPTED
            item.reviewer_note = request.reviewer_note
        self.session.add(
            DatasetFieldReviewRevision(
                dataset_record_id=record.id,
                field_name=field_name,
                action=request.action,
                previous_value=_json_compatible(previous_value),
                new_value=_json_compatible(new_value),
                previous_state=str(previous_state.get("extraction_state", "unknown")),
                new_state=new_state,
                reviewer_note=request.reviewer_note,
                reviewer_label=request.reviewer_label,
                evidence_backed=bool(evidence) if new_value is not None else False,
                evidence_ids=[str(item.id) for item in evidence],
            )
        )
        self.session.flush()
        return record

    def _approve(self, record: DatasetRecord) -> None:
        incomplete: list[str] = []
        for field_name in DATASET_FIELDS:
            state = self._field_state(record, field_name)
            value = record.current_values.get(field_name)
            if state.get("review_status") == "unreviewed":
                incomplete.append(field_name)
            if value is not None and not state.get("evidence_backed"):
                incomplete.append(f"{field_name} (missing evidence)")
        if incomplete:
            raise DatasetReviewError(
                "All fields require explicit review and non-null fields require evidence: "
                + ", ".join(dict.fromkeys(incomplete))
            )
        record.review_status = DatasetReviewStatus.APPROVED

    def _record(self, record_id: UUID) -> DatasetRecord:
        record = self.session.get(DatasetRecord, record_id)
        if record is None:
            raise DatasetNotFoundError(f"Dataset record does not exist: {record_id}")
        return record

    @staticmethod
    def _field_state(record: DatasetRecord, field_name: str) -> dict[str, Any]:
        raw = record.field_states.get(field_name, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _field_evidence(self, record_id: UUID, field_name: str) -> list[FieldEvidence]:
        return list(
            self.session.scalars(
                select(FieldEvidence).where(
                    FieldEvidence.dataset_record_id == record_id,
                    FieldEvidence.field_name == field_name,
                )
            )
        )

    def _resolve_evidence(
        self, record: DatasetRecord, field_name: str, evidence_ids: Sequence[UUID]
    ) -> list[FieldEvidence]:
        if not evidence_ids:
            return []
        evidence = list(
            self.session.scalars(
                select(FieldEvidence).where(
                    FieldEvidence.id.in_(evidence_ids),
                    FieldEvidence.dataset_record_id == record.id,
                    FieldEvidence.field_name == field_name,
                )
            )
        )
        if len(evidence) != len(set(evidence_ids)):
            raise InvalidDatasetEvidenceError(
                "Review evidence must belong to the same dataset record and field"
            )
        return evidence

    @staticmethod
    def _validate_value(field_name: str, value: Any) -> None:
        if field_name in LIST_FIELDS:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                raise DatasetReviewError(f"{field_name} must be a non-empty string list")
        elif field_name in INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DatasetReviewError(f"{field_name} must be a non-negative integer")
        elif not isinstance(value, str) or not value.strip():
            raise DatasetReviewError(f"{field_name} must be a non-empty string")


class DatasetEvidenceService:
    """Attach a human-selected exact passage after strict provenance resolution."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record_id: UUID, request: HumanFieldEvidenceCreate) -> FieldEvidence:
        record = self.session.get(DatasetRecord, record_id)
        if record is None:
            raise DatasetNotFoundError(f"Dataset record does not exist: {record_id}")
        document = self.session.get(SourceDocument, request.document_id)
        if document is None or document.corpus_version_id != record.corpus_version_id:
            raise InvalidDatasetEvidenceError(
                "Evidence document must belong to the dataset record corpus version"
            )
        element = (
            self.session.get(DocumentElement, request.element_id)
            if request.element_id is not None
            else None
        )
        chunk = self.session.get(Chunk, request.chunk_id) if request.chunk_id is not None else None
        if request.element_id is not None and (
            element is None or element.document_id != document.id
        ):
            raise InvalidDatasetEvidenceError("Evidence element does not belong to the document")
        if request.chunk_id is not None and (
            chunk is None
            or chunk.document_id != document.id
            or chunk.corpus_version_id != record.corpus_version_id
        ):
            raise InvalidDatasetEvidenceError(
                "Evidence chunk does not belong to the document and corpus version"
            )
        if (
            element is not None
            and chunk is not None
            and str(element.id) not in chunk.source_element_ids
        ):
            raise InvalidDatasetEvidenceError(
                "Evidence element is not a source of the selected chunk"
            )
        source_texts = [item.text for item in (element, chunk) if item is not None]
        selected_text = request.selected_text.strip()
        if not selected_text or not any(selected_text in text for text in source_texts):
            raise InvalidDatasetEvidenceError(
                "Selected evidence text must be an exact passage from its source"
            )
        resolved_page = element.page_number if element is not None else chunk.page_start  # type: ignore[union-attr]
        if request.page_number is not None:
            page_matches = request.page_number == resolved_page
            if chunk is not None and chunk.page_start is not None and chunk.page_end is not None:
                page_matches = chunk.page_start <= request.page_number <= chunk.page_end
            if not page_matches:
                raise InvalidDatasetEvidenceError("Evidence page does not match source provenance")
            resolved_page = request.page_number
        evidence = FieldEvidence(
            dataset_record_id=record.id,
            field_name=request.field_name,
            document_id=document.id,
            page_number=resolved_page,
            element_id=element.id if element else None,
            chunk_id=chunk.id if chunk else None,
            supporting_text=selected_text,
            original_supporting_text=selected_text,
            extraction_method="human_selection",
            model_confidence_label=None,
            review_status=FieldReviewStatus.UNREVIEWED,
            reviewer_note=request.reviewer_note,
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence


class DatasetCatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_records(
        self,
        *,
        corpus_version_id: UUID | None = None,
        search: str | None = None,
        domain: str | None = None,
        modality: str | None = None,
        task_type: str | None = None,
        language: str | None = None,
        license_name: str | None = None,
        review_status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[DatasetRecord]:
        statement = select(DatasetRecord)
        if corpus_version_id is not None:
            statement = statement.where(DatasetRecord.corpus_version_id == corpus_version_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(DatasetRecord.name.ilike(pattern), DatasetRecord.description.ilike(pattern))
            )
        if domain:
            statement = statement.where(DatasetRecord.domain == domain)
        if license_name:
            statement = statement.where(DatasetRecord.license == license_name)
        if review_status:
            statement = statement.where(DatasetRecord.review_status == review_status)
        records = list(
            self.session.scalars(statement.order_by(DatasetRecord.created_at, DatasetRecord.id))
        )
        if modality:
            records = [item for item in records if modality in (item.modalities or [])]
        if task_type:
            records = [item for item in records if task_type in (item.task_types or [])]
        if language:
            records = [item for item in records if language in (item.languages or [])]
        return records[offset : offset + limit]

    def get(self, record_id: UUID) -> DatasetRecord:
        record = self.session.get(DatasetRecord, record_id)
        if record is None:
            raise DatasetNotFoundError(f"Dataset record does not exist: {record_id}")
        return record

    def evidence(self, record_id: UUID) -> list[FieldEvidence]:
        return list(
            self.session.scalars(
                select(FieldEvidence)
                .where(FieldEvidence.dataset_record_id == record_id)
                .order_by(FieldEvidence.field_name, FieldEvidence.id)
            )
        )

    def revisions(self, record_id: UUID) -> list[DatasetFieldReviewRevision]:
        return list(
            self.session.scalars(
                select(DatasetFieldReviewRevision)
                .where(DatasetFieldReviewRevision.dataset_record_id == record_id)
                .order_by(DatasetFieldReviewRevision.created_at, DatasetFieldReviewRevision.id)
            )
        )

    def comparison(self, record_ids: Iterable[UUID]) -> tuple[UUID, dict[str, Any]]:
        ids = list(record_ids)
        records = list(self.session.scalars(select(DatasetRecord).where(DatasetRecord.id.in_(ids))))
        by_id = {record.id: record for record in records}
        if len(by_id) != len(ids):
            raise DatasetNotFoundError("One or more comparison dataset records do not exist")
        ordered = [by_id[item] for item in ids]
        corpus_ids = {record.corpus_version_id for record in ordered}
        if len(corpus_ids) != 1:
            raise DatasetReviewError("Compared records must use the same corpus version")
        fields = {
            field_name: {
                str(record.id): {
                    "value": record.current_values.get(field_name),
                    "state": record.field_states.get(field_name),
                }
                for record in ordered
            }
            for field_name in DATASET_FIELDS
        }
        return next(iter(corpus_ids)), {"fields": fields, "records": ordered}

    def export(self, record_id: UUID) -> dict[str, Any]:
        record = self.get(record_id)
        return {
            "schema_version": "ragscope.dataset-record-export.v1",
            "record": {
                "id": str(record.id),
                "corpus_version_id": str(record.corpus_version_id),
                "source_document_id": str(record.source_document_id),
                "values": record.current_values,
                "field_states": record.field_states,
                "review_status": record.review_status,
                "extraction_status": record.extraction_status,
                "extraction_configuration": record.extraction_configuration,
            },
            "evidence": [self._row_dict(item) for item in self.evidence(record.id)],
            "correction_history": [self._row_dict(item) for item in self.revisions(record.id)],
            "exported_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _row_dict(row: Any) -> dict[str, Any]:
        return {
            column.name: _json_compatible(getattr(row, column.name))
            for column in row.__table__.columns
        }
