from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Benchmark,
    BenchmarkAnnotationStatus,
    BenchmarkEvidenceReference,
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    BenchmarkVersion,
    BenchmarkVersionStatus,
    Chunk,
    CorpusVersion,
    CorpusVersionStatus,
    DocumentElement,
    SourceDocument,
)

from .schemas import (
    BenchmarkCreate,
    BenchmarkQuestionCreate,
    BenchmarkQuestionRead,
    BenchmarkQuestionUpdate,
    BenchmarkRead,
    BenchmarkVersionCreate,
    BenchmarkVersionRead,
    EvidenceReferenceCreate,
    EvidenceReferenceRead,
    EvidenceSetCreate,
    EvidenceSetRead,
    LeakageCheckRead,
)


class BenchmarkService:
    """Application service for human labels over immutable corpus provenance."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_benchmark(self, payload: BenchmarkCreate) -> BenchmarkRead:
        benchmark = Benchmark(name=payload.name, description=payload.description)
        self.session.add(benchmark)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "BENCHMARK_NAME_CONFLICT",
                "A benchmark with this name already exists.",
            ) from exc
        return BenchmarkRead.model_validate(benchmark)

    def list_benchmarks(self, *, offset: int = 0, limit: int = 100) -> list[BenchmarkRead]:
        rows = self.session.scalars(
            select(Benchmark)
            .order_by(Benchmark.created_at, Benchmark.id)
            .offset(offset)
            .limit(limit)
        )
        return [BenchmarkRead.model_validate(row) for row in rows]

    def get_benchmark(self, benchmark_id: UUID) -> BenchmarkRead:
        return BenchmarkRead.model_validate(self._benchmark(benchmark_id))

    def create_version(
        self, benchmark_id: UUID, payload: BenchmarkVersionCreate
    ) -> BenchmarkVersionRead:
        self._benchmark(benchmark_id)
        corpus_version = self.session.get(CorpusVersion, payload.corpus_version_id)
        if corpus_version is None:
            raise DomainError(
                "CORPUS_VERSION_NOT_FOUND",
                "The selected corpus version does not exist.",
                status_code=404,
            )
        if corpus_version.status is not CorpusVersionStatus.READY:
            raise DomainError(
                "CORPUS_VERSION_NOT_READY",
                "Benchmark versions require a ready corpus version with stable source provenance.",
            )
        version_number = payload.version
        if version_number is None:
            current = self.session.scalar(
                select(func.max(BenchmarkVersion.version)).where(
                    BenchmarkVersion.benchmark_id == benchmark_id
                )
            )
            version_number = int(current or 0) + 1
        version = BenchmarkVersion(
            benchmark_id=benchmark_id,
            corpus_version_id=payload.corpus_version_id,
            version=version_number,
            status=BenchmarkVersionStatus.DRAFT,
            notes=payload.notes,
        )
        self.session.add(version)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "BENCHMARK_VERSION_CONFLICT",
                "This benchmark version number already exists.",
            ) from exc
        return self._version_read(version)

    def list_versions(self, benchmark_id: UUID) -> list[BenchmarkVersionRead]:
        self._benchmark(benchmark_id)
        versions = self.session.scalars(
            select(BenchmarkVersion)
            .where(BenchmarkVersion.benchmark_id == benchmark_id)
            .order_by(BenchmarkVersion.version)
        )
        return [self._version_read(version) for version in versions]

    def get_version(self, version_id: UUID) -> BenchmarkVersionRead:
        return self._version_read(self._version(version_id))

    def create_question(
        self, version_id: UUID, payload: BenchmarkQuestionCreate
    ) -> BenchmarkQuestionRead:
        version = self._version(version_id)
        self._assert_draft(version)
        if payload.annotation_status.value == "reviewed":
            self._validate_complete_values(
                answerable=payload.answerable,
                reference_answer=payload.reference_answer,
                answer_criteria=payload.answer_criteria,
                unanswerable_explanation=payload.unanswerable_explanation,
                evidence_sets=[],
            )
        question = BenchmarkQuestion(
            benchmark_version_id=version.id,
            question_text=payload.question_text,
            question_type=payload.question_type.value,
            difficulty=payload.difficulty.value,
            answerable=payload.answerable,
            expected_answerability=(
                payload.expected_answerability.value
                if payload.expected_answerability is not None
                else ("answerable" if payload.answerable else "unanswerable")
            ),
            reference_answer=payload.reference_answer,
            answer_criteria=payload.answer_criteria,
            unanswerable_explanation=payload.unanswerable_explanation,
            required_document_ids=[],
            required_chunk_ids=[],
            tags=payload.tags,
            annotation_notes=payload.annotation_notes,
            annotation_status=payload.annotation_status.value,
            leakage_warning=False,
            leakage_score=None,
            model_suggestion={},
        )
        self.session.add(question)
        self.session.flush()
        return self.get_question(question.id)

    def list_questions(self, version_id: UUID) -> list[BenchmarkQuestionRead]:
        self._version(version_id)
        rows = self.session.scalars(
            self._question_query()
            .where(BenchmarkQuestion.benchmark_version_id == version_id)
            .order_by(BenchmarkQuestion.created_at, BenchmarkQuestion.id)
        ).unique()
        return [self._question_read(row) for row in rows]

    def get_question(self, question_id: UUID) -> BenchmarkQuestionRead:
        question = self.session.scalar(
            self._question_query().where(BenchmarkQuestion.id == question_id)
        )
        if question is None:
            raise DomainError(
                "BENCHMARK_QUESTION_NOT_FOUND",
                "The benchmark question does not exist.",
                status_code=404,
            )
        return self._question_read(question)

    def update_question(
        self, question_id: UUID, payload: BenchmarkQuestionUpdate
    ) -> BenchmarkQuestionRead:
        question = self._question(question_id)
        self._assert_draft(question.benchmark_version)
        values = payload.model_dump(exclude_unset=True)
        for field_name in (
            "question_type",
            "difficulty",
            "annotation_status",
            "expected_answerability",
        ):
            value = values.get(field_name)
            if value is not None:
                values[field_name] = value.value
        label_fields = {
            "question_text",
            "question_type",
            "difficulty",
            "answerable",
            "expected_answerability",
            "reference_answer",
            "answer_criteria",
            "unanswerable_explanation",
            "tags",
        }
        if (
            _value(question.annotation_status) == "reviewed"
            and "annotation_status" not in values
            and label_fields.intersection(values)
        ):
            values["annotation_status"] = BenchmarkAnnotationStatus.IN_REVIEW
        prospective_answerable = values.get("answerable", question.answerable)
        if "answerable" in values and "expected_answerability" not in values:
            values["expected_answerability"] = (
                "answerable" if prospective_answerable else "unanswerable"
            )
        prospective_expected = _value(
            values.get("expected_answerability", question.expected_answerability)
        )
        if bool(prospective_answerable) == (prospective_expected == "unanswerable"):
            raise DomainError(
                "INVALID_EXPECTED_ANSWERABILITY",
                "Expected answerability must agree with the benchmark answerable label.",
                status_code=422,
            )
        prospective_explanation = values.get(
            "unanswerable_explanation", question.unanswerable_explanation
        )
        if not prospective_answerable and not _present(prospective_explanation):
            raise DomainError(
                "UNANSWERABLE_EXPLANATION_REQUIRED",
                "Unanswerable questions require an explanation.",
                status_code=422,
            )
        prospective_status = values.get("annotation_status", question.annotation_status)
        if _value(prospective_status) == "reviewed":
            self._validate_complete_values(
                answerable=bool(prospective_answerable),
                reference_answer=values.get("reference_answer", question.reference_answer),
                answer_criteria=values.get("answer_criteria", question.answer_criteria),
                unanswerable_explanation=prospective_explanation,
                evidence_sets=question.evidence_sets,
            )
        for key, value in values.items():
            setattr(question, key, value)
        self._refresh_leakage(question)
        self.session.flush()
        return self.get_question(question.id)

    def add_evidence_set(
        self, question_id: UUID, payload: EvidenceSetCreate
    ) -> BenchmarkQuestionRead:
        question = self._question(question_id)
        version = question.benchmark_version
        self._assert_draft(version)
        set_number = payload.set_number
        if set_number is None:
            current = self.session.scalar(
                select(func.max(BenchmarkEvidenceSet.set_number)).where(
                    BenchmarkEvidenceSet.benchmark_question_id == question.id
                )
            )
            set_number = int(current or 0) + 1
        existing = self.session.scalar(
            select(BenchmarkEvidenceSet).where(
                BenchmarkEvidenceSet.benchmark_question_id == question.id,
                BenchmarkEvidenceSet.set_number == set_number,
            )
        )
        if existing is not None:
            raise DomainError(
                "EVIDENCE_SET_CONFLICT",
                "An acceptable evidence set with this number already exists.",
            )
        validated_references: list[tuple[EvidenceReferenceCreate, _ValidatedReference]] = []
        seen: set[tuple[UUID, UUID | None, UUID | None, str]] = set()
        for reference_payload in payload.references:
            validated = self._validate_reference(version, reference_payload)
            identity = (
                reference_payload.document_id,
                reference_payload.element_id,
                reference_payload.chunk_id,
                reference_payload.selected_text,
            )
            if identity in seen:
                raise DomainError(
                    "DUPLICATE_EVIDENCE_REFERENCE",
                    "An evidence set cannot contain duplicate source references.",
                    status_code=422,
                )
            seen.add(identity)
            validated_references.append((reference_payload, validated))
        evidence_set = BenchmarkEvidenceSet(
            benchmark_question_id=question.id,
            set_number=set_number,
            description=payload.description,
        )
        question.evidence_sets.append(evidence_set)
        self.session.flush()
        for reference_payload, validated in validated_references:
            evidence_set.references.append(
                BenchmarkEvidenceReference(
                    evidence_set_id=evidence_set.id,
                    document_id=reference_payload.document_id,
                    page_number=validated.page_number,
                    element_id=reference_payload.element_id,
                    chunk_id=reference_payload.chunk_id,
                    selected_text=reference_payload.selected_text,
                    start_offset=reference_payload.start_offset,
                    end_offset=reference_payload.end_offset,
                    evidence_role=reference_payload.evidence_role,
                )
            )
        self.session.flush()
        self._sync_required_ids(question)
        self._refresh_leakage(question)
        if _value(question.annotation_status) == "reviewed":
            question.annotation_status = BenchmarkAnnotationStatus.IN_REVIEW
            self.session.flush()
        return self.get_question(question.id)

    def delete_evidence_set(self, question_id: UUID, evidence_set_id: UUID) -> None:
        question = self._question(question_id)
        self._assert_draft(question.benchmark_version)
        evidence_set = self.session.get(BenchmarkEvidenceSet, evidence_set_id)
        if evidence_set is None or evidence_set.benchmark_question_id != question.id:
            raise DomainError(
                "EVIDENCE_SET_NOT_FOUND",
                "The acceptable evidence set does not exist for this question.",
                status_code=404,
            )
        question.evidence_sets.remove(evidence_set)
        self.session.flush()
        self._sync_required_ids(question)
        self._refresh_leakage(question)
        if _value(question.annotation_status) == "reviewed":
            question.annotation_status = BenchmarkAnnotationStatus.IN_REVIEW
            self.session.flush()

    def leakage_check(self, question_id: UUID) -> LeakageCheckRead:
        question = self._question(question_id)
        score = leakage_score(
            question.question_text,
            [
                reference.selected_text
                for evidence_set in question.evidence_sets
                for reference in evidence_set.references
            ],
        )
        return LeakageCheckRead(warning=score >= 0.8, score=score)

    def freeze_version(self, version_id: UUID) -> BenchmarkVersionRead:
        version = self._version(version_id)
        self._assert_draft(version)
        questions = list(
            self.session.scalars(
                self._question_query()
                .where(BenchmarkQuestion.benchmark_version_id == version.id)
                .order_by(BenchmarkQuestion.created_at, BenchmarkQuestion.id)
            ).unique()
        )
        if not questions:
            raise DomainError(
                "BENCHMARK_VERSION_EMPTY",
                "A benchmark version must contain at least one reviewed question before freezing.",
                status_code=422,
            )
        for question in questions:
            if _value(question.annotation_status) != "reviewed":
                raise DomainError(
                    "BENCHMARK_QUESTION_NOT_REVIEWED",
                    "Every benchmark question must be human-reviewed before freezing.",
                    status_code=422,
                )
            self._validate_complete(question)
        version.status = BenchmarkVersionStatus.FROZEN
        version.frozen_at = datetime.now(UTC)
        self.session.flush()
        return self._version_read(version)

    def _validate_complete(self, question: BenchmarkQuestion) -> None:
        self._validate_complete_values(
            answerable=question.answerable,
            reference_answer=question.reference_answer,
            answer_criteria=question.answer_criteria,
            unanswerable_explanation=question.unanswerable_explanation,
            evidence_sets=question.evidence_sets,
        )

    @staticmethod
    def _validate_complete_values(
        *,
        answerable: bool,
        reference_answer: str | None,
        answer_criteria: str | None,
        unanswerable_explanation: str | None,
        evidence_sets: list[BenchmarkEvidenceSet],
    ) -> None:
        if answerable:
            if not evidence_sets or any(
                not any(
                    reference.evidence_role in {"required", "alternative"}
                    for reference in evidence_set.references
                )
                for evidence_set in evidence_sets
            ):
                raise DomainError(
                    "ANSWERABLE_EVIDENCE_REQUIRED",
                    "Answerable questions require at least one non-empty acceptable evidence set.",
                    status_code=422,
                )
            if not (_present(reference_answer) or _present(answer_criteria)):
                raise DomainError(
                    "REFERENCE_ANSWER_OR_CRITERIA_REQUIRED",
                    "Answerable questions require a reference answer or explicit answer criteria.",
                    status_code=422,
                )
        elif not _present(unanswerable_explanation):
            raise DomainError(
                "UNANSWERABLE_EXPLANATION_REQUIRED",
                "Unanswerable questions require an explanation.",
                status_code=422,
            )

    def _validate_reference(
        self, version: BenchmarkVersion, payload: EvidenceReferenceCreate
    ) -> _ValidatedReference:
        document = self.session.get(SourceDocument, payload.document_id)
        if document is None:
            raise _invalid_evidence("The referenced document does not exist.")
        if document.corpus_version_id != version.corpus_version_id:
            raise _invalid_evidence("Evidence must belong to the benchmark corpus version.")

        source_texts: list[str] = []
        derived_pages: list[int] = []
        element: DocumentElement | None = None
        chunk: Chunk | None = None
        if payload.element_id is not None:
            element = self.session.get(DocumentElement, payload.element_id)
            if element is None or element.document_id != document.id:
                raise _invalid_evidence("The element does not belong to the referenced document.")
            source_texts.append(element.text)
            if element.page_number is not None:
                derived_pages.append(element.page_number)
        if payload.chunk_id is not None:
            chunk = self.session.get(Chunk, payload.chunk_id)
            if (
                chunk is None
                or chunk.document_id != document.id
                or chunk.corpus_version_id != version.corpus_version_id
            ):
                raise _invalid_evidence("The chunk does not belong to the referenced document.")
            source_texts.append(chunk.text)
            if chunk.page_start is not None:
                derived_pages.append(chunk.page_start)
        if element is not None and chunk is not None:
            if str(element.id) not in chunk.source_element_ids:
                raise _invalid_evidence("The element is not a provenance source for the chunk.")
        if not any(_contains_text(text, payload.selected_text) for text in source_texts):
            raise _invalid_evidence("Selected evidence text must occur in its source object.")
        if payload.start_offset is not None and payload.end_offset is not None:
            if not any(
                text[payload.start_offset : payload.end_offset] == payload.selected_text
                for text in source_texts
            ):
                raise _invalid_evidence(
                    "The selected offsets do not resolve to the selected source text."
                )
        if payload.page_number is not None:
            if element is not None and element.page_number is not None:
                if payload.page_number != element.page_number:
                    raise _invalid_evidence("The page does not match element provenance.")
            elif chunk is not None and chunk.page_start is not None:
                page_end = chunk.page_end or chunk.page_start
                if not chunk.page_start <= payload.page_number <= page_end:
                    raise _invalid_evidence("The page is outside chunk provenance.")
        return _ValidatedReference(
            page_number=payload.page_number or (derived_pages[0] if derived_pages else None)
        )

    def _sync_required_ids(self, question: BenchmarkQuestion) -> None:
        rows = list(
            self.session.scalars(
                select(BenchmarkEvidenceReference)
                .join(
                    BenchmarkEvidenceSet,
                    BenchmarkEvidenceSet.id == BenchmarkEvidenceReference.evidence_set_id,
                )
                .where(BenchmarkEvidenceSet.benchmark_question_id == question.id)
                .order_by(BenchmarkEvidenceSet.set_number, BenchmarkEvidenceReference.id)
            )
        )
        required_rows = [
            row for row in rows if row.evidence_role in {"required", "alternative"}
        ]
        question.required_document_ids = _unique_ids(row.document_id for row in required_rows)
        question.required_chunk_ids = _unique_ids(
            row.chunk_id for row in required_rows if row.chunk_id is not None
        )
        self.session.flush()

    def _refresh_leakage(self, question: BenchmarkQuestion) -> None:
        score = leakage_score(
            question.question_text,
            [
                reference.selected_text
                for evidence_set in question.evidence_sets
                for reference in evidence_set.references
            ],
        )
        question.leakage_score = score
        question.leakage_warning = score >= 0.8
        self.session.flush()

    def _benchmark(self, benchmark_id: UUID) -> Benchmark:
        benchmark = self.session.get(Benchmark, benchmark_id)
        if benchmark is None:
            raise DomainError(
                "BENCHMARK_NOT_FOUND", "The benchmark does not exist.", status_code=404
            )
        return benchmark

    def _version(self, version_id: UUID) -> BenchmarkVersion:
        version = self.session.get(BenchmarkVersion, version_id)
        if version is None:
            raise DomainError(
                "BENCHMARK_VERSION_NOT_FOUND",
                "The benchmark version does not exist.",
                status_code=404,
            )
        return version

    def _question(self, question_id: UUID) -> BenchmarkQuestion:
        question = self.session.scalar(
            self._question_query().where(BenchmarkQuestion.id == question_id)
        )
        if question is None:
            raise DomainError(
                "BENCHMARK_QUESTION_NOT_FOUND",
                "The benchmark question does not exist.",
                status_code=404,
            )
        return question

    @staticmethod
    def _assert_draft(version: BenchmarkVersion) -> None:
        if version.frozen_at is not None or _value(version.status) == "frozen":
            raise DomainError(
                "BENCHMARK_VERSION_IMMUTABLE",
                "Frozen benchmark versions and their annotations are immutable.",
            )

    @staticmethod
    def _question_query() -> Select[tuple[BenchmarkQuestion]]:
        return select(BenchmarkQuestion).options(
            selectinload(BenchmarkQuestion.benchmark_version),
            selectinload(BenchmarkQuestion.evidence_sets).selectinload(
                BenchmarkEvidenceSet.references
            ),
        )

    def _version_read(self, version: BenchmarkVersion) -> BenchmarkVersionRead:
        question_count = self.session.scalar(
            select(func.count(BenchmarkQuestion.id)).where(
                BenchmarkQuestion.benchmark_version_id == version.id
            )
        )
        return BenchmarkVersionRead(
            id=version.id,
            benchmark_id=version.benchmark_id,
            corpus_version_id=version.corpus_version_id,
            version=version.version,
            status=_value(version.status),
            notes=version.notes,
            created_at=version.created_at,
            frozen_at=version.frozen_at,
            question_count=int(question_count or 0),
        )

    @staticmethod
    def _question_read(question: BenchmarkQuestion) -> BenchmarkQuestionRead:
        evidence_sets = [
            EvidenceSetRead(
                id=evidence_set.id,
                benchmark_question_id=evidence_set.benchmark_question_id,
                set_number=evidence_set.set_number,
                description=evidence_set.description,
                created_at=evidence_set.created_at,
                references=[
                    EvidenceReferenceRead(
                        id=reference.id,
                        evidence_set_id=reference.evidence_set_id,
                        document_id=reference.document_id,
                        page_number=reference.page_number,
                        element_id=reference.element_id,
                        chunk_id=reference.chunk_id,
                        selected_text=reference.selected_text,
                        start_offset=reference.start_offset,
                        end_offset=reference.end_offset,
                        evidence_role=reference.evidence_role,
                        created_at=reference.created_at,
                    )
                    for reference in evidence_set.references
                ],
            )
            for evidence_set in sorted(
                question.evidence_sets, key=lambda value: value.set_number
            )
        ]
        return BenchmarkQuestionRead(
            id=question.id,
            benchmark_version_id=question.benchmark_version_id,
            question_text=question.question_text,
            question_type=_value(question.question_type),
            difficulty=_value(question.difficulty),
            answerable=question.answerable,
            expected_answerability=_value(question.expected_answerability),
            reference_answer=question.reference_answer,
            answer_criteria=question.answer_criteria,
            unanswerable_explanation=question.unanswerable_explanation,
            required_document_ids=[UUID(str(value)) for value in question.required_document_ids],
            required_chunk_ids=[UUID(str(value)) for value in question.required_chunk_ids],
            tags=question.tags,
            annotation_notes=question.annotation_notes,
            annotation_status=_value(question.annotation_status),
            leakage_warning=question.leakage_warning,
            leakage_score=question.leakage_score,
            model_suggestion=question.model_suggestion,
            created_at=question.created_at,
            updated_at=question.updated_at,
            acceptable_evidence_sets=evidence_sets,
        )


class _ValidatedReference:
    def __init__(self, *, page_number: int | None) -> None:
        self.page_number = page_number


def leakage_score(question: str, passages: list[str]) -> float:
    """Return deterministic question/passages overlap; this is only a warning."""

    question_tokens = _tokens(question)
    if len(question_tokens) < 4 or not passages:
        return 0.0
    question_text = " ".join(question_tokens)
    scores: list[float] = []
    for passage in passages:
        passage_tokens = _tokens(passage)
        if not passage_tokens:
            continue
        passage_text = " ".join(passage_tokens)
        coverage = sum(token in set(passage_tokens) for token in question_tokens) / len(
            question_tokens
        )
        sequence = SequenceMatcher(None, question_text, passage_text, autojunk=False).ratio()
        contiguous = 1.0 if _contains_contiguous(question_tokens, passage_tokens, 6) else 0.0
        scores.append(max(coverage, sequence, contiguous))
    return round(max(scores, default=0.0), 4)


def _contains_contiguous(left: list[str], right: list[str], length: int) -> bool:
    if len(left) < length or len(right) < length:
        return False
    windows = {tuple(right[index : index + length]) for index in range(len(right) - length + 1)}
    return any(
        tuple(left[index : index + length]) in windows
        for index in range(len(left) - length + 1)
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE)


def _contains_text(source: str, selected: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(value.split()).casefold()

    return normalize(selected) in normalize(source)


def _invalid_evidence(message: str) -> DomainError:
    return DomainError("INVALID_EVIDENCE_REFERENCE", message, status_code=422)


def _present(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _unique_ids(values: Any) -> list[str]:
    seen: set[UUID] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(str(value))
            seen.add(value)
    return output
