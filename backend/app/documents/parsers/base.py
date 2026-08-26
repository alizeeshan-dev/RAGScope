"""Provider-independent normalized document parser contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ElementType = Literal[
    "title", "heading", "paragraph", "list", "table", "caption", "figure", "formula", "other"
]


@dataclass(frozen=True, slots=True)
class BoundingBox:
    left: float
    top: float
    right: float
    bottom: float
    coordinate_system: str = "parser"

    def as_dict(self) -> dict[str, float | str]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "coordinate_system": self.coordinate_system,
        }


@dataclass(frozen=True, slots=True)
class ParseWarning:
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"
    page_number: int | None = None
    element_sequence: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "page_number": self.page_number,
            "element_sequence": self.element_sequence,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class NormalizedElement:
    """Element before persistence; ``local_id`` makes hierarchy deterministic."""

    local_id: str
    parent_local_id: str | None
    element_type: ElementType
    sequence_number: int
    text: str
    page_number: int | None = None
    section_path: tuple[str, ...] = ()
    bounding_box: BoundingBox | None = None
    parser_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseResult:
    parser_id: str
    parser_version: str
    elements: tuple[NormalizedElement, ...]
    warnings: tuple[ParseWarning, ...] = ()
    page_count: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParserConfiguration:
    parser_id: str
    options: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"parser_id": self.parser_id, "options": self.options}


class Parser(Protocol):
    parser_id: str
    media_types: frozenset[str]

    @property
    def parser_version(self) -> str: ...

    def parse(
        self,
        content: bytes,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult: ...
