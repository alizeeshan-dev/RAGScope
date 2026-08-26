"""Chunker contracts and deterministic token accounting."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from backend.app.documents.parsers.base import NormalizedElement

_TOKEN = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]", re.UNICODE)
_NO_SPACE_BEFORE = frozenset(".,;:!?%)]}")
_NO_SPACE_AFTER = frozenset("([{\n")


class Tokenizer:
    """Versioned, dependency-free research baseline tokenizer.

    It counts Unicode word runs (including internal apostrophes) and punctuation as
    tokens.  It is not claimed to match any embedding/generation provider tokenizer.
    """

    tokenizer_id = "unicode-word-punctuation-v1"

    def tokenize(self, text: str) -> list[str]:
        return _TOKEN.findall(text)

    def count(self, text: str) -> int:
        return len(self.tokenize(text))

    def join(self, tokens: list[str]) -> str:
        output = ""
        previous = ""
        for token in tokens:
            if not output:
                output = token
            elif token in _NO_SPACE_BEFORE or previous in _NO_SPACE_AFTER:
                output += token
            else:
                output += " " + token
            previous = token
        return output


@dataclass(frozen=True, slots=True)
class GeneratedChunk:
    sequence_number: int
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    source_element_ids: tuple[str, ...]
    content_hash: str
    metadata: dict[str, object] = field(default_factory=dict)


class Chunker(Protocol):
    chunker_id: str

    def chunk(self, elements: list[NormalizedElement]) -> list[GeneratedChunk]: ...


def common_section_path(elements: list[NormalizedElement]) -> tuple[str, ...]:
    paths = [element.section_path for element in elements if element.section_path]
    if not paths:
        return ()
    common = list(paths[0])
    for path in paths[1:]:
        mismatch = next(
            (
                index
                for index, (left, right) in enumerate(zip(common, path, strict=False))
                if left != right
            ),
            min(len(common), len(path)),
        )
        common = common[:mismatch]
    return tuple(common)


def stable_chunk_hash(
    *,
    chunker_id: str,
    configuration: dict[str, object],
    sequence_number: int,
    text: str,
    page_start: int | None,
    page_end: int | None,
    section_path: tuple[str, ...],
) -> str:
    # Source DB UUIDs and timestamps are intentionally excluded. Sequence/page/section
    # coordinates plus text/config identify the reproducible content boundary.
    payload = {
        "chunker_id": chunker_id,
        "configuration": configuration,
        "sequence_number": sequence_number,
        "text": text,
        "page_start": page_start,
        "page_end": page_end,
        "section_path": list(section_path),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pages(elements: list[NormalizedElement]) -> tuple[int | None, int | None]:
    values = [element.page_number for element in elements if element.page_number is not None]
    return (min(values), max(values)) if values else (None, None)


def unique_source_ids(elements: list[NormalizedElement]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(element.local_id for element in elements))
