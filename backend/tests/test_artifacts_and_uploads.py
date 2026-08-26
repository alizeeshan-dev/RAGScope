from __future__ import annotations

from pathlib import Path

import pytest
from backend.app.artifacts.service import (
    ArtifactSecurityError,
    LocalArtifactStore,
    sanitize_filename,
)
from backend.app.documents.errors import FileTooLargeError, UnsupportedFileTypeError
from backend.app.documents.validation import validate_upload


def test_artifact_store_uses_safe_random_key_and_is_immutable(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact = store.put_bytes(
        b"research",
        media_type="text/plain",
        original_filename="../../paper.txt",
        producing_operation="test",
    )

    assert artifact.original_filename == "paper.txt"
    assert "paper.txt" not in artifact.storage_key
    assert store.read_bytes(artifact.storage_key) == b"research"
    with pytest.raises(ValueError, match="read-only"):
        store.open(artifact.storage_key, "wb")


@pytest.mark.parametrize("key", ["../secret", "..\\secret", "/etc/passwd", "C:\\Windows\\win.ini"])
def test_artifact_store_rejects_traversal(tmp_path: Path, key: str) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactSecurityError):
        store.read_bytes(key)


def test_sanitize_filename_strips_path_and_controls() -> None:
    assert sanitize_filename("../bad\x00/name?.pdf") == "name_.pdf"


def test_upload_validation_hashes_supported_text() -> None:
    result = validate_upload(
        filename="study.md",
        content=b"# Study\n\nFindings.",
        claimed_media_type="text/markdown; charset=utf-8",
        max_size_bytes=1024,
    )
    assert result.media_type == "text/markdown"
    assert len(result.content_hash) == 64


def test_upload_validation_rejects_mismatches_and_size() -> None:
    with pytest.raises(UnsupportedFileTypeError, match="signature"):
        validate_upload(
            filename="fake.pdf",
            content=b"not a pdf",
            claimed_media_type="application/pdf",
            max_size_bytes=1024,
        )
    with pytest.raises(UnsupportedFileTypeError, match="binary"):
        validate_upload(
            filename="fake.txt",
            content=b"text\x00payload",
            claimed_media_type="text/plain",
            max_size_bytes=1024,
        )
    with pytest.raises(FileTooLargeError):
        validate_upload(
            filename="large.txt",
            content=b"12345",
            claimed_media_type="text/plain",
            max_size_bytes=4,
        )
