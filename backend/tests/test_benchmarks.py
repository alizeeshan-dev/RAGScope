from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
from backend.app.benchmarks.api import router as benchmark_router
from backend.app.benchmarks.schemas import (
    AnnotationStatus,
    BenchmarkCreate,
    BenchmarkQuestionCreate,
    BenchmarkQuestionUpdate,
    BenchmarkVersionCreate,
    Difficulty,
    EvidenceReferenceCreate,
    EvidenceSetCreate,
    ExpectedAnswerability,
    QuestionType,
)
from backend.app.benchmarks.service import BenchmarkService, leakage_score
from backend.app.core.errors import DomainError, install_error_handlers
from backend.app.db.models import (
    Chunk,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    DocumentElement,
    ParseStatus,
    SourceDocument,
)
from backend.app.db.session import get_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session


@dataclass
class SourceGraph:
    corpus_version_id: UUID
    document_id: UUID
    element_id: UUID
    chunk_id: UUID
    text: str


def _source_graph(session: Session, *, suffix: str = "one") -> SourceGraph:
    corpus = Corpus(name=f"Corpus {suffix}")
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
        title=f"Dataset paper {suffix}",
        file_hash=(suffix.encode().hex() + "0" * 64)[:64],
        mime_type="application/pdf",
        parse_status=ParseStatus.READY,
        page_count=4,
    )
    session.add(document)
    session.flush()
    text = (
        "The Atlas dataset contains exactly ten thousand annotated images. "
        "It is released under the CC BY 4.0 license."
    )
    element = DocumentElement(
        document_id=document.id,
        element_type="paragraph",
        sequence_number=1,
        page_number=2,
        text=text,
    )
    session.add(element)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        corpus_version_id=version.id,
        chunker_id="fixture-v1",
        sequence_number=1,
        text=text,
        token_count=len(text.split()),
        page_start=2,
        page_end=2,
        source_element_ids=[str(element.id)],
        content_hash=("a" + suffix.encode().hex() + "0" * 64)[:64],
    )
    session.add(chunk)
    session.flush()
    return SourceGraph(version.id, document.id, element.id, chunk.id, text)


def _draft(session: Session, source: SourceGraph) -> tuple[BenchmarkService, UUID]:
    service = BenchmarkService(session)
    benchmark = service.create_benchmark(
        BenchmarkCreate(name="Dataset evidence benchmark", description="Human labels")
    )
    version = service.create_version(
        benchmark.id, BenchmarkVersionCreate(corpus_version_id=source.corpus_version_id)
    )
    return service, version.id


def _answerable_question(service: BenchmarkService, version_id: UUID) -> UUID:
    question = service.create_question(
        version_id,
        BenchmarkQuestionCreate(
            question_text="How many annotated images does the Atlas dataset contain?",
            question_type=QuestionType.DIRECT_FACT_LOOKUP,
            difficulty=Difficulty.EASY,
            answerable=True,
            reference_answer="The Atlas dataset contains 10,000 annotated images.",
            tags=["dataset", "count", "dataset"],
        ),
    )
    return question.id


def _evidence(source: SourceGraph, *, set_number: int | None = None) -> EvidenceSetCreate:
    selected = "The Atlas dataset contains exactly ten thousand annotated images."
    return EvidenceSetCreate(
        set_number=set_number,
        references=[
            EvidenceReferenceCreate(
                document_id=source.document_id,
                page_number=2,
                element_id=source.element_id,
                chunk_id=source.chunk_id,
                selected_text=selected,
                start_offset=0,
                end_offset=len(selected),
            )
        ],
    )


def test_draft_question_evidence_review_and_freeze_are_reproducible(
    session: Session,
) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    question_id = _answerable_question(service, version_id)

    question = service.add_evidence_set(question_id, _evidence(source))
    assert len(question.acceptable_evidence_sets) == 1
    assert question.required_document_ids == [source.document_id]
    assert question.required_chunk_ids == [source.chunk_id]
    assert question.model_suggestion == {}

    reviewed = service.update_question(
        question_id,
        BenchmarkQuestionUpdate(
            annotation_status=AnnotationStatus.REVIEWED,
            annotation_notes="Checked against the paper.",
        ),
    )
    assert reviewed.annotation_status == "reviewed"
    frozen = service.freeze_version(version_id)
    assert frozen.status == "frozen"
    assert frozen.frozen_at is not None

    with pytest.raises(DomainError, match="immutable") as update_error:
        service.update_question(
            question_id,
            BenchmarkQuestionUpdate(question_text="Mutated after freeze?"),
        )
    assert update_error.value.code == "BENCHMARK_VERSION_IMMUTABLE"
    with pytest.raises(DomainError) as evidence_error:
        service.add_evidence_set(question_id, _evidence(source, set_number=2))
    assert evidence_error.value.code == "BENCHMARK_VERSION_IMMUTABLE"


def test_answerable_question_cannot_be_reviewed_or_frozen_without_evidence(
    session: Session,
) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    question_id = _answerable_question(service, version_id)

    with pytest.raises(DomainError) as review_error:
        service.update_question(
            question_id,
            BenchmarkQuestionUpdate(annotation_status=AnnotationStatus.REVIEWED),
        )
    assert review_error.value.code == "ANSWERABLE_EVIDENCE_REQUIRED"
    with pytest.raises(DomainError) as freeze_error:
        service.freeze_version(version_id)
    assert freeze_error.value.code == "BENCHMARK_QUESTION_NOT_REVIEWED"


def test_benchmark_version_requires_ready_corpus(session: Session) -> None:
    source = _source_graph(session)
    corpus_version = session.get(CorpusVersion, source.corpus_version_id)
    assert corpus_version is not None
    corpus_version.status = CorpusVersionStatus.DRAFT
    service = BenchmarkService(session)
    benchmark = service.create_benchmark(BenchmarkCreate(name="Not-ready benchmark"))

    with pytest.raises(DomainError) as error:
        service.create_version(
            benchmark.id,
            BenchmarkVersionCreate(corpus_version_id=source.corpus_version_id),
        )
    assert error.value.code == "CORPUS_VERSION_NOT_READY"


def test_unanswerable_requires_explanation_but_not_fake_evidence(session: Session) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    with pytest.raises(ValidationError):
        BenchmarkQuestionCreate(
            question_text="What is the absent acquisition price?",
            question_type=QuestionType.UNANSWERABLE,
            difficulty=Difficulty.MEDIUM,
            answerable=False,
        )

    question = service.create_question(
        version_id,
        BenchmarkQuestionCreate(
            question_text="What is the absent acquisition price?",
            question_type=QuestionType.UNANSWERABLE,
            difficulty=Difficulty.MEDIUM,
            answerable=False,
            unanswerable_explanation="The requested fact is absent from every source.",
            annotation_status=AnnotationStatus.REVIEWED,
        ),
    )
    assert question.acceptable_evidence_sets == []
    assert question.required_chunk_ids == []
    assert service.freeze_version(version_id).status == "frozen"


def test_alternative_evidence_sets_stay_distinct_and_derive_required_ids(
    session: Session,
) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    question_id = _answerable_question(service, version_id)
    first = service.add_evidence_set(question_id, _evidence(source, set_number=1))
    assert [item.set_number for item in first.acceptable_evidence_sets] == [1]

    second = service.add_evidence_set(
        question_id,
        EvidenceSetCreate(
            set_number=2,
            description="The license sentence independently supports another criterion.",
            references=[
                EvidenceReferenceCreate(
                    document_id=source.document_id,
                    element_id=source.element_id,
                    selected_text="It is released under the CC BY 4.0 license.",
                    evidence_role="alternative",
                )
            ],
        ),
    )
    assert [item.set_number for item in second.acceptable_evidence_sets] == [1, 2]
    assert second.acceptable_evidence_sets[1].references[0].evidence_role == "alternative"
    assert second.required_document_ids == [source.document_id]
    assert second.required_chunk_ids == [source.chunk_id]

    with pytest.raises(DomainError) as duplicate_error:
        service.add_evidence_set(question_id, _evidence(source, set_number=2))
    assert duplicate_error.value.code == "EVIDENCE_SET_CONFLICT"


def test_evidence_ids_text_and_corpus_provenance_are_validated(session: Session) -> None:
    source = _source_graph(session)
    foreign = _source_graph(session, suffix="foreign")
    service, version_id = _draft(session, source)
    question_id = _answerable_question(service, version_id)

    with pytest.raises(DomainError) as corpus_error:
        service.add_evidence_set(question_id, _evidence(foreign))
    assert corpus_error.value.code == "INVALID_EVIDENCE_REFERENCE"

    with pytest.raises(DomainError) as text_error:
        service.add_evidence_set(
            question_id,
            EvidenceSetCreate(
                references=[
                    EvidenceReferenceCreate(
                        document_id=source.document_id,
                        chunk_id=source.chunk_id,
                        selected_text="This invented passage does not exist.",
                    )
                ]
            ),
        )
    assert text_error.value.code == "INVALID_EVIDENCE_REFERENCE"

    with pytest.raises(DomainError) as page_error:
        service.add_evidence_set(
            question_id,
            EvidenceSetCreate(
                references=[
                    EvidenceReferenceCreate(
                        document_id=source.document_id,
                        page_number=99,
                        element_id=source.element_id,
                        selected_text=(
                            "The Atlas dataset contains exactly ten thousand annotated images."
                        ),
                    )
                ]
            ),
        )
    assert page_error.value.code == "INVALID_EVIDENCE_REFERENCE"


def test_leakage_warning_is_deterministic_and_non_blocking(session: Session) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    question = service.create_question(
        version_id,
        BenchmarkQuestionCreate(
            question_text="The Atlas dataset contains exactly ten thousand annotated images",
            question_type=QuestionType.DIRECT_FACT_LOOKUP,
            difficulty=Difficulty.EASY,
            answerable=True,
            answer_criteria="State the image count.",
        ),
    )
    updated = service.add_evidence_set(question.id, _evidence(source))
    check = service.leakage_check(question.id)
    assert check.warning is True
    assert check.score == 1.0
    assert updated.leakage_warning is True
    # Warnings never reject an otherwise valid human annotation.
    service.update_question(
        question.id,
        BenchmarkQuestionUpdate(annotation_status=AnnotationStatus.REVIEWED),
    )
    assert service.freeze_version(version_id).status == "frozen"
    assert leakage_score("What license applies?", []) == 0.0


def test_label_or_evidence_changes_require_human_review_again(session: Session) -> None:
    source = _source_graph(session)
    service, version_id = _draft(session, source)
    question_id = _answerable_question(service, version_id)
    service.add_evidence_set(question_id, _evidence(source))
    service.update_question(
        question_id,
        BenchmarkQuestionUpdate(annotation_status=AnnotationStatus.REVIEWED),
    )

    changed = service.update_question(
        question_id,
        BenchmarkQuestionUpdate(question_text="How large is the Atlas image dataset?"),
    )
    assert changed.annotation_status == "in_review"
    service.update_question(
        question_id,
        BenchmarkQuestionUpdate(annotation_status=AnnotationStatus.REVIEWED),
    )
    changed_again = service.add_evidence_set(
        question_id,
        EvidenceSetCreate(
            set_number=2,
            references=[
                EvidenceReferenceCreate(
                    document_id=source.document_id,
                    element_id=source.element_id,
                    selected_text="It is released under the CC BY 4.0 license.",
                    evidence_role="alternative",
                )
            ],
        ),
    )
    assert changed_again.annotation_status == "in_review"


def test_benchmark_api_exposes_create_list_and_read_workflow(session: Session) -> None:
    source = _source_graph(session)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(benchmark_router, prefix="/api/v1")

    def override_db() -> object:
        yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/benchmarks",
            json={"name": "API benchmark", "description": "Human-only labels"},
        )
        assert created.status_code == 201
        benchmark_id = created.json()["id"]
        version = client.post(
            f"/api/v1/benchmarks/{benchmark_id}/versions",
            json={"corpus_version_id": str(source.corpus_version_id)},
        )
        assert version.status_code == 201
        version_id = version.json()["id"]
        question = client.post(
            f"/api/v1/benchmark-versions/{version_id}/questions",
            json={
                "question_text": "Which fact is absent?",
                "question_type": "unanswerable",
                "difficulty": "medium",
                "answerable": False,
                "unanswerable_explanation": "The corpus does not state the fact.",
            },
        )
        assert question.status_code == 201
        question_id = question.json()["id"]
        fetched = client.get(f"/api/v1/benchmark-questions/{question_id}")
        assert fetched.status_code == 200
        assert fetched.json()["model_suggestion"] == {}
        assert client.get("/api/v1/benchmarks").json()[0]["name"] == "API benchmark"


def test_partial_answerability_is_explicit_and_consistent(session: Session) -> None:
    source = _source_graph(session, suffix="partial")
    service, version_id = _draft(session, source)
    question = service.create_question(
        version_id,
        BenchmarkQuestionCreate(
            question_text="Which Atlas attributes are stated?",
            question_type=QuestionType.DIRECT_FACT_LOOKUP,
            difficulty=Difficulty.MEDIUM,
            answerable=True,
            expected_answerability=ExpectedAnswerability.PARTIALLY_ANSWERABLE,
            reference_answer="Only the image count is stated.",
        ),
    )
    assert question.expected_answerability == "partially_answerable"

    with pytest.raises(ValidationError):
        BenchmarkQuestionCreate(
            question_text="Which fact is absent?",
            question_type=QuestionType.UNANSWERABLE,
            difficulty=Difficulty.EASY,
            answerable=False,
            expected_answerability=ExpectedAnswerability.PARTIALLY_ANSWERABLE,
            unanswerable_explanation="The fact is absent.",
        )
