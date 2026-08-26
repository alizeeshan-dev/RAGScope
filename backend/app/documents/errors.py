"""Stable document-domain errors exposed by the API."""

from __future__ import annotations

from backend.app.core.errors import DomainError


class DocumentError(DomainError):
    code = "DOCUMENT_ERROR"
    status_code = 400

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(self.code, message, status_code=self.status_code)
        self.details = details or {}


class CorpusVersionImmutableError(DocumentError):
    code = "CORPUS_VERSION_IMMUTABLE"
    status_code = 409


class UnsupportedFileTypeError(DocumentError):
    code = "UNSUPPORTED_FILE_TYPE"
    status_code = 415


class FileTooLargeError(DocumentError):
    code = "FILE_TOO_LARGE"
    status_code = 413


class DuplicateDocumentError(DocumentError):
    code = "DUPLICATE_DOCUMENT"
    status_code = 409


class DocumentParseError(DocumentError):
    code = "DOCUMENT_PARSE_FAILED"
    status_code = 422


class ParserUnavailableError(DocumentParseError):
    code = "PARSER_UNAVAILABLE"
    status_code = 503
