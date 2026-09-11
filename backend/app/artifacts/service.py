"""Content-hashed, random-keyed, traversal-resistant local artifact storage.

The store deliberately never uses the client supplied filename as a path.  Files are
addressed by a random identifier and verified relative to the configured root before
every access.  Database metadata can refer to ``storage_key`` without exposing an
arbitrary local filesystem path to API callers.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import BinaryIO, cast
from uuid import UUID, uuid4


class ArtifactSecurityError(ValueError):
    """Raised when an artifact key attempts to escape the storage root."""


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    id: UUID
    storage_key: str
    content_hash: str
    size_bytes: int
    media_type: str
    original_filename: str | None
    producing_operation: str
    configuration: dict[str, object]
    created_at: datetime


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_filename(filename: str | None) -> str | None:
    """Return a display-only filename with path/control characters removed."""

    if not filename:
        return None
    name = PurePath(filename.replace("\\", "/")).name
    name = unicodedata.normalize("NFKC", name)
    name = "".join(char for char in name if char.isprintable())
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    return name[:255] or "upload"


class LocalArtifactStore:
    """Store immutable bytes atomically beneath a dedicated data directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, storage_key: str) -> Path:
        key = PurePath(storage_key.replace("\\", "/"))
        if key.is_absolute() or any(part in {"", ".", ".."} for part in key.parts):
            raise ArtifactSecurityError("Invalid artifact storage key")
        path = (self.root / Path(*key.parts)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactSecurityError("Artifact path escapes storage root") from exc
        return path

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        producing_operation: str,
        original_filename: str | None = None,
        configuration: dict[str, object] | None = None,
    ) -> ArtifactDescriptor:
        artifact_id = uuid4()
        content_hash = hashlib.sha256(content).hexdigest()
        storage_key = f"{artifact_id.hex[:2]}/{artifact_id.hex}"
        destination = self._resolve_key(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            # Random keys make replacement practically impossible; fail closed anyway.
            if destination.exists():
                raise FileExistsError("Artifact identifier collision")
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

        return ArtifactDescriptor(
            id=artifact_id,
            storage_key=storage_key,
            content_hash=content_hash,
            size_bytes=len(content),
            media_type=media_type,
            original_filename=sanitize_filename(original_filename),
            producing_operation=producing_operation,
            configuration=dict(configuration or {}),
            created_at=datetime.now(UTC),
        )

    def put_stream(
        self,
        stream: BinaryIO,
        **metadata: object,
    ) -> ArtifactDescriptor:
        # Upload validation enforces limits before this storage boundary.  This helper
        # remains intentionally simple and delegates to the only write implementation.
        return self.put_bytes(stream.read(), **metadata)  # type: ignore[arg-type]

    def open(self, storage_key: str, mode: str = "rb") -> BinaryIO:
        if mode not in {"rb"}:
            raise ValueError("Artifacts are immutable and may only be opened read-only")
        return cast(BinaryIO, self._resolve_key(storage_key).open(mode))

    def read_bytes(self, storage_key: str) -> bytes:
        return self._resolve_key(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        return self._resolve_key(storage_key).is_file()
