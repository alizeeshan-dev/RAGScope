"""Database-backed ingestion, parsing, metadata, and chunk generation services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.artifacts.service import ArtifactDescriptor, LocalArtifactStore
from backend.app.db.models import (
    Artifact,
    Chunk,
    CorpusVersion,
    CorpusVersionStatus,
    DocumentElement,
    ParseStatus,
    SourceDocument,
)
from backend.app.documents.chunkers.base import Chunker, GeneratedChunk
from backend.app.documents.errors import (
    CorpusVersionImmutableError,
    DocumentParseError,
    DuplicateDocumentError,
)
from backend.app.documents.parsers import DoclingPdfParser, MarkdownParser, Parser, PlainTextParser
from backend.app.documents.parsers.base import NormalizedElement, ParserConfiguration, ParseResult
from backend.app.documents.validation import validate_upload


def assert_version_editable(version: CorpusVersion) -> None:
    if version.status != CorpusVersionStatus.DRAFT or version.frozen_at is not None:
        raise CorpusVersionImmutableError(
            "Documents and derived content may only change on draft versions"
        )


def parser_for_media_type(media_type: str) -> Parser:
    if media_type == "application/pdf":
        return DoclingPdfParser()
    if media_type in {"text/markdown", "text/x-markdown"}:
        return MarkdownParser()
    if media_type == "text/plain":
        return PlainTextParser()
    raise DocumentParseError(f"No parser is registered for media type {media_type}")


class DocumentService:
    def __init__(
        self,
        session: Session,
        artifact_store: LocalArtifactStore,
        *,
        max_upload_bytes: int,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.max_upload_bytes = max_upload_bytes

    def upload(
        self,
        *,
        corpus_version_id: UUID,
        filename: str,
        content: bytes,
        claimed_media_type: str | None,
        title: str | None = None,
    ) -> SourceDocument:
        version = self.session.get(CorpusVersion, corpus_version_id)
        if version is None:
            raise LookupError("Corpus version not found")
        assert_version_editable(version)
        validated = validate_upload(
            filename=filename,
            content=content,
            claimed_media_type=claimed_media_type,
            max_size_bytes=self.max_upload_bytes,
        )
        existing = self.session.scalar(
            select(SourceDocument.id).where(
                SourceDocument.corpus_version_id == corpus_version_id,
                SourceDocument.file_hash == validated.content_hash,
            )
        )
        if existing is not None:
            raise DuplicateDocumentError(
                "This file already exists in the corpus version",
                details={"document_id": str(existing), "file_hash": validated.content_hash},
            )

        descriptor = self.artifact_store.put_bytes(
            validated.content,
            media_type=validated.media_type,
            original_filename=validated.filename,
            producing_operation="document-upload",
            configuration={"validation": "upload-v1"},
        )
        document = SourceDocument(
            corpus_version_id=version.id,
            title=title or Path(validated.filename).stem,
            authors=[],
            source_type=validated.extension.removeprefix("."),
            file_hash=validated.content_hash,
            mime_type=validated.media_type,
            parse_status=ParseStatus.PENDING,
            parse_warnings=[],
            metadata_provenance={
                "title": {"source": "upload", "original_filename": validated.filename},
            },
        )
        self.session.add(document)
        self.session.flush()
        self.session.add(
            self._artifact_model(descriptor, document=document, artifact_type="original")
        )
        version.document_count += 1
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DuplicateDocumentError("This file already exists in the corpus version") from exc
        return document

    def parse(
        self,
        document: SourceDocument,
        *,
        parser: Parser | None = None,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult:
        assert_version_editable(document.corpus_version)
        artifact = self.session.scalar(
            select(Artifact).where(
                Artifact.document_id == document.id,
                Artifact.artifact_type == "original",
            )
        )
        if artifact is None:
            raise DocumentParseError("Original artifact metadata is missing")
        selected_parser = parser or parser_for_media_type(document.mime_type)
        if configuration is None:
            configuration = ParserConfiguration(
                parser_id=selected_parser.parser_id,
                options=dict(document.corpus_version.parser_configuration or {}),
            )
        elif configuration.parser_id != selected_parser.parser_id:
            raise DocumentParseError(
                "Parser configuration does not match the selected parser",
                details={
                    "configured_parser_id": configuration.parser_id,
                    "selected_parser_id": selected_parser.parser_id,
                },
            )
        document.parse_status = ParseStatus.PARSING
        document.parse_warnings = []
        self.session.flush()
        try:
            content = self.artifact_store.read_bytes(artifact.storage_key)
            result = selected_parser.parse(content, configuration)
            self._persist_parse_result(document, result)
            document.parse_status = ParseStatus.READY
            document.parse_warnings = [warning.as_dict() for warning in result.warnings]
            document.page_count = result.page_count
            self._persist_parsed_artifact(document, result, configuration)
            self.session.flush()
            return result
        except DocumentParseError as exc:
            # No normalized rows are inserted before successful parser return. A
            # previously successful representation is removed so FAILED cannot look
            # partially ready.
            self.session.execute(
                delete(DocumentElement).where(DocumentElement.document_id == document.id)
            )
            self.session.execute(delete(Chunk).where(Chunk.document_id == document.id))
            document.parse_status = ParseStatus.FAILED
            document.parse_warnings = [
                {"code": exc.code, "message": exc.message, "severity": "warning"}
            ]
            self.session.flush()
            raise
        except Exception as exc:
            self.session.execute(
                delete(DocumentElement).where(DocumentElement.document_id == document.id)
            )
            self.session.execute(delete(Chunk).where(Chunk.document_id == document.id))
            document.parse_status = ParseStatus.FAILED
            document.parse_warnings = [
                {"code": "DOCUMENT_PARSE_FAILED", "message": "Parser failed", "severity": "warning"}
            ]
            self.session.flush()
            raise DocumentParseError("Parser failed") from exc

    def update_metadata(
        self,
        document: SourceDocument,
        changes: dict[str, object],
    ) -> SourceDocument:
        assert_version_editable(document.corpus_version)
        allowed = {"title", "authors", "publication_year", "source_uri", "license_information"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise ValueError(f"Unsupported metadata fields: {sorted(unexpected)}")
        provenance = dict(document.metadata_provenance or {})
        changed_at = datetime.now(UTC).isoformat()
        for field, value in changes.items():
            previous = getattr(document, field)
            setattr(document, field, value)
            history = list(provenance.get(field, {}).get("manual_history", []))
            history.append({"changed_at": changed_at, "previous": previous, "value": value})
            provenance[field] = {"source": "manual", "manual_history": history}
        document.metadata_provenance = provenance
        self.session.flush()
        return document

    def _persist_parse_result(self, document: SourceDocument, result: ParseResult) -> None:
        # Parsed elements are the provenance source for every derived chunk. Remove
        # all stale derivations before replacing the element graph.
        self.session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        self.session.execute(
            delete(DocumentElement).where(DocumentElement.document_id == document.id)
        )
        local_to_uuid = {element.local_id: uuid4() for element in result.elements}
        for element in result.elements:
            parent_id = (
                local_to_uuid.get(element.parent_local_id) if element.parent_local_id else None
            )
            self.session.add(
                DocumentElement(
                    id=local_to_uuid[element.local_id],
                    document_id=document.id,
                    parent_element_id=parent_id,
                    element_type=element.element_type,
                    sequence_number=element.sequence_number,
                    page_number=element.page_number,
                    section_path=list(element.section_path),
                    text=element.text,
                    bounding_box=element.bounding_box.as_dict() if element.bounding_box else None,
                    parser_metadata={
                        **element.parser_metadata,
                        "parser_id": result.parser_id,
                        "parser_version": result.parser_version,
                        "normalized_local_id": element.local_id,
                    },
                )
            )

    def _persist_parsed_artifact(
        self,
        document: SourceDocument,
        result: ParseResult,
        configuration: ParserConfiguration | None,
    ) -> None:
        payload = {
            "schema_version": "normalized-document-v1",
            "parser_id": result.parser_id,
            "parser_version": result.parser_version,
            "page_count": result.page_count,
            "elements": [self._element_payload(element) for element in result.elements],
            "warnings": [warning.as_dict() for warning in result.warnings],
            "metadata": result.metadata,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = self.artifact_store.put_bytes(
            encoded,
            media_type="application/vnd.ragscope.normalized-document+json",
            producing_operation="document-parse",
            configuration=(
                configuration.as_dict()
                if configuration
                else {"parser_id": result.parser_id}
            ),
        )
        self.session.add(
            self._artifact_model(
                descriptor,
                document=document,
                artifact_type="normalized-document",
                producer_version=result.parser_version,
            )
        )

    @staticmethod
    def _element_payload(element: NormalizedElement) -> dict[str, object]:
        return {
            "id": element.local_id,
            "parent_id": element.parent_local_id,
            "element_type": element.element_type,
            "sequence_number": element.sequence_number,
            "page_number": element.page_number,
            "section_path": list(element.section_path),
            "text": element.text,
            "bounding_box": element.bounding_box.as_dict() if element.bounding_box else None,
            "parser_metadata": element.parser_metadata,
        }

    @staticmethod
    def _artifact_model(
        descriptor: ArtifactDescriptor,
        *,
        document: SourceDocument,
        artifact_type: str,
        producer_version: str | None = None,
    ) -> Artifact:
        return Artifact(
            id=descriptor.id,
            corpus_version_id=document.corpus_version_id,
            document_id=document.id,
            artifact_type=artifact_type,
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=descriptor.original_filename,
            producing_operation=descriptor.producing_operation,
            producer_version=producer_version,
            configuration=descriptor.configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )


class ChunkService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def generate_for_version(self, version: CorpusVersion, chunker: Chunker) -> list[Chunk]:
        assert_version_editable(version)
        documents = self.session.scalars(
            select(SourceDocument)
            .where(SourceDocument.corpus_version_id == version.id)
            .order_by(SourceDocument.created_at, SourceDocument.id)
        ).all()
        if not documents:
            raise DocumentParseError(
                "At least one parsed document is required for chunk generation"
            )
        if any(document.parse_status != ParseStatus.READY for document in documents):
            raise DocumentParseError(
                "All documents must be parsed successfully before chunk generation"
            )

        persisted: list[Chunk] = []
        for document in documents:
            self.session.execute(
                delete(Chunk).where(
                    Chunk.document_id == document.id,
                    Chunk.chunker_id == chunker.chunker_id,
                )
            )
            rows = self.session.scalars(
                select(DocumentElement)
                .where(DocumentElement.document_id == document.id)
                .order_by(DocumentElement.sequence_number)
            ).all()
            normalized = [self._normalized(row) for row in rows]
            generated = chunker.chunk(normalized)
            persisted.extend(self._persist_chunks(document, version, chunker.chunker_id, generated))
        self.session.flush()
        return persisted

    def _persist_chunks(
        self,
        document: SourceDocument,
        version: CorpusVersion,
        chunker_id: str,
        generated: list[GeneratedChunk],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for value in generated:
            chunk = Chunk(
                document_id=document.id,
                corpus_version_id=version.id,
                chunker_id=chunker_id,
                sequence_number=value.sequence_number,
                text=value.text,
                token_count=value.token_count,
                page_start=value.page_start,
                page_end=value.page_end,
                section_path=list(value.section_path),
                source_element_ids=list(value.source_element_ids),
                content_hash=value.content_hash,
                chunk_metadata=value.metadata,
            )
            self.session.add(chunk)
            chunks.append(chunk)
        return chunks

    @staticmethod
    def _normalized(row: DocumentElement) -> NormalizedElement:
        # Stored UUIDs are correct provenance references. Reproducible content hashes
        # intentionally do not include them (see stable_chunk_hash).
        return NormalizedElement(
            local_id=str(row.id),
            parent_local_id=str(row.parent_element_id) if row.parent_element_id else None,
            element_type=row.element_type,  # type: ignore[arg-type]
            sequence_number=row.sequence_number,
            page_number=row.page_number,
            section_path=tuple(row.section_path),
            text=row.text,
            bounding_box=None,
            parser_metadata=row.parser_metadata,
        )
