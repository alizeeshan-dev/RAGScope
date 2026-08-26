"""Upload validation with extension, signature, MIME, and size checks."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import PurePath

from backend.app.artifacts.service import sanitize_filename
from backend.app.documents.errors import FileTooLargeError, UnsupportedFileTypeError

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
TEXT_MIME_ALIASES = {"text/plain", "text/markdown", "text/x-markdown", "application/octet-stream"}


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    content: bytes
    filename: str
    extension: str
    media_type: str
    content_hash: str


def _looks_like_binary(data: bytes) -> bool:
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    controls = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return controls / len(sample) > 0.03


def validate_upload(
    *,
    filename: str,
    content: bytes,
    claimed_media_type: str | None,
    max_size_bytes: int,
) -> ValidatedUpload:
    if len(content) > max_size_bytes:
        raise FileTooLargeError(
            f"Upload exceeds the {max_size_bytes} byte limit",
            details={"size_bytes": len(content), "max_size_bytes": max_size_bytes},
        )

    safe_name = sanitize_filename(filename)
    if not safe_name:
        raise UnsupportedFileTypeError("A supported filename extension is required")
    extension = PurePath(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported extension: {extension or '(none)'}",
            details={"supported_extensions": sorted(SUPPORTED_EXTENSIONS)},
        )

    expected = SUPPORTED_EXTENSIONS[extension]
    claimed = (claimed_media_type or "").split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_type(safe_name)[0]
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise UnsupportedFileTypeError("PDF extension does not match the file signature")
        if claimed and claimed not in {"application/pdf", "application/octet-stream"}:
            raise UnsupportedFileTypeError("Claimed media type does not match PDF content")
        media_type = "application/pdf"
    else:
        if claimed and claimed not in TEXT_MIME_ALIASES:
            raise UnsupportedFileTypeError("Claimed media type does not match text content")
        if _looks_like_binary(content):
            raise UnsupportedFileTypeError("Text upload appears to contain binary data")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFileTypeError("Text uploads must be valid UTF-8") from exc
        media_type = expected if extension != ".txt" else "text/plain"

    return ValidatedUpload(
        content=content,
        filename=safe_name,
        extension=extension,
        media_type=media_type or guessed or expected,
        content_hash=hashlib.sha256(content).hexdigest(),
    )
