"""Conservative redaction for observable trace metadata.

Trace summaries are deliberately small, but configuration and provider metadata can
still contain credentials.  This module redacts both sensitive key names and known
secret values before anything reaches a trace row or export.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?keys?|authorization|cookies?|credentials?|"
    r"database[_-]?url|password|passwd|private[_-]?key|secrets?|sessions?|tokens?)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_AUTHORIZATION_VALUE = re.compile(r"^(?:bearer|basic)\s+\S+", re.IGNORECASE)
_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_URL_SECRET_PARAMETER = re.compile(
    r"(?P<prefix>[?&](?:api[_-]?key|password|secret|token)=)[^&\s]+",
    re.IGNORECASE,
)


def configured_sensitive_values(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Return non-trivial credential values from sensitive environment variables.

    Variable names are inspected, never emitted. Very short values are ignored to
    avoid replacing common substrings in harmless observable metadata.
    """

    source = os.environ if environ is None else environ
    return frozenset(
        value
        for key, value in source.items()
        if _is_sensitive_key(key) and len(value) >= 6
    )


def redact(
    value: Any,
    *,
    sensitive_values: Iterable[str] = (),
) -> Any:
    """Recursively return a JSON-safe, credential-redacted representation."""

    secrets = tuple(secret for secret in sensitive_values if len(secret) >= 6)
    return _redact(value, secrets=secrets, seen=set())


def _redact(value: Any, *, secrets: tuple[str, ...], seen: set[int]) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _redact_string(value, secrets)
    if isinstance(value, Enum):
        return _redact(value.value, secrets=secrets, seen=seen)
    if isinstance(value, UUID | date | datetime | Path):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{type(value).__name__}:{len(value)} bytes>"

    identity = id(value)
    if identity in seen:
        return "<recursive>"
    seen.add(identity)
    try:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                result[key] = (
                    REDACTED
                    if _is_sensitive_key(key)
                    else _redact(item, secrets=secrets, seen=seen)
                )
            return result
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [_redact(item, secrets=secrets, seen=seen) for item in value]
        if isinstance(value, set | frozenset):
            normalized = [_redact(item, secrets=secrets, seen=seen) for item in value]
            return sorted(normalized, key=repr)
        return _redact_string(str(value), secrets)
    finally:
        seen.remove(identity)


def _redact_string(value: str, secrets: tuple[str, ...]) -> str:
    if _AUTHORIZATION_VALUE.match(value.strip()):
        return REDACTED
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, REDACTED)
    redacted = _URL_USERINFO.sub(rf"\g<scheme>{REDACTED}@", redacted)
    redacted = _URL_SECRET_PARAMETER.sub(rf"\g<prefix>{REDACTED}", redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).casefold()
    return _SENSITIVE_KEY.search(normalized) is not None
