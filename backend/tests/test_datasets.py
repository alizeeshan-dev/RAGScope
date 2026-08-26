from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.datasets.errors import (
    DatasetReviewError,
    InvalidDatasetEvidenceError,
    InvalidDatasetOutputError,
)
from backend.app.datasets.schemas import (
    DATASET_FIELDS,
    DatasetExtractionOutput,
    DatasetExtractionRequest,
    DatasetReviewRequest,
    ExtractionStrategy,
    HumanFieldEvidenceCreate,
)
from backend.app.datasets.service import (
    DatasetCatalogService,
    DatasetEvidenceService,
    DatasetExtractionService,
    DatasetReviewService,
)
from backend.app.datasets.strategies import DeterministicDatasetGenerationProvider
from backend.app.db.models import (
    Artifact,
    Chunk,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    DatasetExtractionStatus,
    DatasetFieldReviewRevision,
    DatasetRecord,
    DatasetReviewStatus,
    DocumentElement,
    FieldEvidence,
    Job,
    JobStatus,
    ParseStatus,
    SourceDocument,
)
from backend.app.providers.base import StructuredGenerationResult
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session


def _source_document(session: Session, *, two_chunks: bool = False) -> tuple[SourceDocument, Job]:
    corpus = Corpus(name=f"datasets-{uuid4()}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="v1",
        status=CorpusVersionStatus.READY,
        document_count=1,
    )
    session.add(version)
    session.flush()
    document = SourceDocument(
        corpus_version_id=version.id,
        title="Example Research Dataset",
        authors=[],
        file_hash="a" * 64,
        mime_type="application/pdf",
        parse_status=ParseStatus.READY,
    )
    session.add(document)
    session.flush()
    first = DocumentElement(
        document_id=document.id,
        element_type="paragraph",
        sequence_number=1,
        page_number=1,
        section_path=["Introduction"],
        text="This introductory paragraph discusses a scientific study.",
    )
    session.add(first)
    session.flush()
    first_chunk = Chunk(
        document_id=document.id,
        corpus_version_id=version.id,
        chunker_id="test-v1",
        sequence_number=1,
        text=first.text,
        token_count=8,
        page_start=1,
        page_end=1,
        section_path=first.section_path,
        source_element_ids=[str(first.id)],
        content_hash="b" * 64,
    )
    session.add(first_chunk)
    if two_chunks:
        second = DocumentElement(
            document_id=document.id,
            element_type="paragraph",
            sequence_number=2,
            page_number=3,
            section_path=["Dataset"],
            text=(
                "The dataset contains 1200 annotated instances from 80 participants, "
                "and is available under a research license."
            ),
        )
        session.add(second)
        session.flush()
        session.add(
            Chunk(
                document_id=document.id,
                corpus_version_id=version.id,
                chunker_id="test-v1",
                sequence_number=2,
                text=second.text,
                token_count=17,
                page_start=3,
                page_end=3,
                section_path=second.section_path,
                source_element_ids=[str(second.id)],
                content_hash="c" * 64,
            )
        )
    job = Job(
        job_type="dataset_extraction",
        input_reference={"document_id": str(document.id)},
        status=JobStatus.RUNNING,
        progress_total=1,
    )
    session.add(job)
    session.flush()
    return document, job


def _not_stated_payload() -> dict[str, Any]:
    return {
        field: {"state": "not_stated", "value": None, "evidence_ids": []}
        for field in DATASET_FIELDS
    }


class StaticDatasetProvider(DeterministicDatasetGenerationProvider):
    def __init__(self, content: str) -> None:
        self.content = content

    def generate_structured(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> StructuredGenerationResult:
        assert response_schema is not None
        return StructuredGenerationResult(
            content=self.content,
            raw_response=json.dumps({"content": self.content, "api_key": "[REDACTED]"}),
            model_id=self.model_id,
            provider_id=self.provider_id,
        )


def _extract(
    session: Session,
    tmp_path: Path,
    *,
    strategy: ExtractionStrategy = ExtractionStrategy.BASELINE,
    two_chunks: bool = False,
) -> tuple[SourceDocument, DatasetRecord]:
    document, job = _source_document(session, two_chunks=two_chunks)
    request = DatasetExtractionRequest(
        strategy=strategy,
        retrieval_candidate_count=1,
    )
    record = DatasetExtractionService(
        session,
        LocalArtifactStore(tmp_path / "artifacts"),
        DeterministicDatasetGenerationProvider(),
    ).extract(document.id, request, job=job)
    return document, record


def test_extraction_schema_distinguishes_not_stated_and_rejects_unsupported_fields() -> None:
    payload = _not_stated_payload()
    output = DatasetExtractionOutput.model_validate(payload)
    assert output.license.state.value == "not_stated"
    assert output.license.value is None

    payload["name"] = {"state": "stated", "value": "Dataset", "evidence_ids": []}
    with pytest.raises(ValidationError, match="requires evidence"):
        DatasetExtractionOutput.model_validate(payload)

    payload = _not_stated_payload()
    payload["unsupported_catalog_field"] = {"state": "not_stated"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DatasetExtractionOutput.model_validate(payload)


@pytest.mark.parametrize(
    "strategy", [ExtractionStrategy.BASELINE, ExtractionStrategy.RETRIEVAL_ASSISTED]
)
def test_both_strategies_persist_model_snapshot_and_source_evidence(
    session: Session, tmp_path: Path, strategy: ExtractionStrategy
) -> None:
    document, record = _extract(session, tmp_path, strategy=strategy, two_chunks=True)
    session.flush()
    evidence = list(
        session.scalars(select(FieldEvidence).where(FieldEvidence.dataset_record_id == record.id))
    )

    assert record.extraction_status is DatasetExtractionStatus.VALID
    assert record.strategy == strategy.value
    assert record.original_values["name"] in evidence[0].supporting_text
    assert record.current_values == record.original_values
    assert len(evidence) == 1
    assert evidence[0].document_id == document.id
    assert evidence[0].chunk_id is not None
    assert evidence[0].supporting_text == evidence[0].original_supporting_text
    assert record.raw_response_artifact_id is not None
    assert record.structured_result_artifact_id is not None
    if strategy is ExtractionStrategy.RETRIEVAL_ASSISTED:
        assert evidence[0].page_number == 3


def test_invalid_structured_output_keeps_raw_artifact(session: Session, tmp_path: Path) -> None:
    document, job = _source_document(session)
    service = DatasetExtractionService(
        session, LocalArtifactStore(tmp_path / "artifacts"), StaticDatasetProvider("not-json")
    )
    with pytest.raises(InvalidDatasetOutputError):
        service.extract(document.id, DatasetExtractionRequest(), job=job)

    artifacts = list(
        session.scalars(
            select(Artifact).where(Artifact.artifact_type == "dataset_extraction_raw_response")
        )
    )
    assert len(artifacts) == 1
    assert artifacts[0].job_id == job.id


def test_model_cannot_reference_an_arbitrary_evidence_id(session: Session, tmp_path: Path) -> None:
    document, job = _source_document(session)
    payload = _not_stated_payload()
    payload["name"] = {
        "state": "stated",
        "value": "Invented",
        "evidence_ids": ["E999"],
    }
    provider = StaticDatasetProvider(json.dumps(payload))
    with pytest.raises(InvalidDatasetEvidenceError, match="unknown evidence IDs"):
        DatasetExtractionService(
            session, LocalArtifactStore(tmp_path / "artifacts"), provider
        ).extract(document.id, DatasetExtractionRequest(), job=job)


def test_human_edit_preserves_original_and_records_manual_assertion(
    session: Session, tmp_path: Path
) -> None:
    _, record = _extract(session, tmp_path)
    original_name = record.original_values["name"]
    reviewed = DatasetReviewService(session).review(
        record.id,
        DatasetReviewRequest(
            action="edit",
            field_name="name",
            value="Human corrected name",
            reviewer_note="Corrected from the paper title.",
            reviewer_label="reviewer-1",
        ),
    )
    revision = session.scalar(
        select(DatasetFieldReviewRevision).where(
            DatasetFieldReviewRevision.dataset_record_id == record.id
        )
    )

    assert reviewed.original_values["name"] == original_name
    assert reviewed.current_values["name"] == "Human corrected name"
    assert reviewed.name == "Human corrected name"
    assert reviewed.field_states["name"]["value_source"] == "human"
    assert reviewed.field_states["name"]["evidence_backed"] is False
    assert revision is not None and revision.evidence_backed is False
    with pytest.raises(DatasetReviewError, match="missing evidence"):
        DatasetReviewService(session).review(
            record.id, DatasetReviewRequest(action="approve_record")
        )


@pytest.mark.parametrize(
    ("action", "expected_state"),
    [("reject", "rejected"), ("clear", "unknown"), ("mark_not_stated", "not_stated")],
)
def test_reject_clear_and_not_stated_preserve_model_output(
    session: Session,
    tmp_path: Path,
    action: str,
    expected_state: str,
) -> None:
    _, record = _extract(session, tmp_path / action)
    original = record.original_values["name"]
    reviewed = DatasetReviewService(session).review(
        record.id,
        DatasetReviewRequest.model_validate(
            {"action": action, "field_name": "name", "reviewer_label": "human"}
        ),
    )
    revision = session.scalar(
        select(DatasetFieldReviewRevision).where(
            DatasetFieldReviewRevision.dataset_record_id == record.id
        )
    )

    assert reviewed.original_values["name"] == original
    assert reviewed.current_values["name"] is None
    assert reviewed.field_states["name"]["extraction_state"] == expected_state
    assert revision is not None and revision.action == action


def test_human_selected_evidence_is_provenance_validated_and_can_back_an_edit(
    session: Session, tmp_path: Path
) -> None:
    document, record = _extract(session, tmp_path)
    chunk = session.scalar(select(Chunk).where(Chunk.document_id == document.id))
    assert chunk is not None
    evidence = DatasetEvidenceService(session).add(
        record.id,
        HumanFieldEvidenceCreate(
            field_name="description",
            document_id=document.id,
            page_number=1,
            chunk_id=chunk.id,
            selected_text="introductory paragraph discusses a scientific study",
            reviewer_note="Exact passage selected by reviewer.",
        ),
    )
    reviewed = DatasetReviewService(session).review(
        record.id,
        DatasetReviewRequest(
            action="edit",
            field_name="description",
            value="A scientific study dataset.",
            evidence_ids=[evidence.id],
            reviewer_label="reviewer-1",
        ),
    )
    assert reviewed.field_states["description"]["evidence_backed"] is True
    assert evidence.extraction_method == "human_selection"

    unrelated_document, _ = _source_document(session)
    with pytest.raises(InvalidDatasetEvidenceError, match="corpus version"):
        DatasetEvidenceService(session).add(
            record.id,
            HumanFieldEvidenceCreate(
                field_name="description",
                document_id=unrelated_document.id,
                chunk_id=chunk.id,
                selected_text="scientific study",
            ),
        )


def test_explicit_field_review_can_approve_evidence_backed_record(
    session: Session, tmp_path: Path
) -> None:
    _, record = _extract(session, tmp_path)
    service = DatasetReviewService(session)
    service.review(
        record.id,
        DatasetReviewRequest(action="accept", field_name="name", reviewer_label="human"),
    )
    for field_name in DATASET_FIELDS:
        if field_name != "name":
            service.review(
                record.id,
                DatasetReviewRequest(
                    action="mark_not_stated",
                    field_name=field_name,
                    reviewer_label="human",
                ),
            )
    approved = service.review(record.id, DatasetReviewRequest(action="approve_record"))

    assert approved.review_status is DatasetReviewStatus.APPROVED
    assert approved.field_states["name"]["review_status"] == "accepted"
    assert approved.field_states["license"]["extraction_state"] == "not_stated"


def test_catalog_filters_comparison_and_export(session: Session, tmp_path: Path) -> None:
    _, first = _extract(session, tmp_path / "one")
    second = DatasetRecord(
        corpus_version_id=first.corpus_version_id,
        source_document_id=first.source_document_id,
        strategy="baseline",
        name="Second Dataset",
        domain="medicine",
        modalities=["image"],
        task_types=["classification"],
        languages=["English"],
        original_values={"name": "Second Dataset"},
        current_values={"name": "Second Dataset"},
        field_states={"name": {"extraction_state": "stated"}},
        extraction_configuration={"field_schema_version": "dataset-record.v1"},
        extraction_status=DatasetExtractionStatus.VALID,
        review_status=DatasetReviewStatus.APPROVED,
    )
    session.add(second)
    session.flush()
    catalog = DatasetCatalogService(session)

    assert catalog.list_records(domain="medicine") == [second]
    assert catalog.list_records(modality="image") == [second]
    corpus_id, comparison = catalog.comparison([first.id, second.id])
    assert corpus_id == first.corpus_version_id
    assert comparison["fields"]["name"][str(second.id)]["value"] == "Second Dataset"
    exported = catalog.export(first.id)
    assert exported["schema_version"] == "ragscope.dataset-record-export.v1"
    assert exported["record"]["values"]["name"] == first.current_values["name"]
