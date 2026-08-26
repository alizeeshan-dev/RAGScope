"""Deterministic Markdown and UTF-8 plain-text parsers."""

from __future__ import annotations

import re

from backend.app.documents.errors import DocumentParseError
from backend.app.documents.parsers.base import (
    NormalizedElement,
    ParserConfiguration,
    ParseResult,
    ParseWarning,
)
from backend.app.documents.parsers.quality import quality_warnings

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError("Document is not valid UTF-8") from exc


def _element(
    sequence: int,
    element_type: str,
    text: str,
    *,
    section_path: tuple[str, ...] = (),
    parent: str | None = None,
    metadata: dict[str, object] | None = None,
) -> NormalizedElement:
    return NormalizedElement(
        local_id=f"element-{sequence}",
        parent_local_id=parent,
        element_type=element_type,  # type: ignore[arg-type]
        sequence_number=sequence,
        text=text.strip(),
        page_number=None,
        section_path=section_path,
        bounding_box=None,
        parser_metadata={"provenance": "source-order", **(metadata or {})},
    )


class PlainTextParser:
    parser_id = "plain-text"
    parser_version = "1"
    media_types = frozenset({"text/plain"})

    def parse(
        self,
        content: bytes,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult:
        text = _decode(content).replace("\r\n", "\n").replace("\r", "\n")
        blocks = [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]
        elements = [
            _element(index, "paragraph", block, metadata={"page_provenance_available": False})
            for index, block in enumerate(blocks)
        ]
        warnings = quality_warnings(elements)
        warnings.insert(
            0,
            _page_provenance_warning("Plain-text input does not contain reliable page coordinates"),
        )
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            elements=tuple(elements),
            warnings=tuple(warnings),
            page_count=None,
        )


def _page_provenance_warning(message: str) -> ParseWarning:
    return ParseWarning("PAGE_PROVENANCE_UNAVAILABLE", message, severity="info")


class MarkdownParser:
    parser_id = "markdown"
    parser_version = "1"
    media_types = frozenset({"text/markdown", "text/x-markdown"})

    def parse(
        self,
        content: bytes,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult:
        source = _decode(content).replace("\r\n", "\n").replace("\r", "\n")
        lines = source.split("\n")
        elements: list[NormalizedElement] = []
        heading_stack: list[tuple[int, str, str]] = []
        index = 0
        sequence = 0

        def add(kind: str, value: str, metadata: dict[str, object] | None = None) -> None:
            nonlocal sequence
            path = tuple(item[1] for item in heading_stack)
            parent = heading_stack[-1][2] if heading_stack else None
            elements.append(
                _element(
                    sequence,
                    kind,
                    value,
                    section_path=path,
                    parent=parent,
                    metadata={"page_provenance_available": False, **(metadata or {})},
                )
            )
            sequence += 1

        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            heading = _HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent = heading_stack[-1][2] if heading_stack else None
                local_id = f"element-{sequence}"
                path = tuple(item[1] for item in heading_stack) + (title,)
                elements.append(
                    _element(
                        sequence,
                        "title" if level == 1 and not elements else "heading",
                        title,
                        section_path=path,
                        parent=parent,
                        metadata={"heading_level": level, "page_provenance_available": False},
                    )
                )
                heading_stack.append((level, title, local_id))
                sequence += 1
                index += 1
                continue

            # A pipe row followed by a Markdown separator starts a table.  Preserve
            # the Markdown serialization and record row/column information.
            if "|" in line and index + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[index + 1]):
                table_lines = [line, lines[index + 1]]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    table_lines.append(lines[index])
                    index += 1
                column_count = len([cell for cell in line.strip().strip("|").split("|")])
                add(
                    "table",
                    "\n".join(table_lines),
                    {
                        "serialization": "markdown",
                        "row_count": len(table_lines) - 1,
                        "column_count": column_count,
                    },
                )
                continue

            list_lines: list[str] = []
            while index < len(lines) and _LIST.match(lines[index]):
                list_lines.append(lines[index].strip())
                index += 1
            if list_lines:
                add("list", "\n".join(list_lines), {"serialization": "markdown"})
                continue

            paragraph: list[str] = [line.strip()]
            index += 1
            while index < len(lines) and lines[index].strip():
                if _HEADING.match(lines[index]) or _LIST.match(lines[index]):
                    break
                table_starts = (
                    "|" in lines[index]
                    and index + 1 < len(lines)
                    and _TABLE_SEPARATOR.match(lines[index + 1])
                )
                if table_starts:
                    break
                paragraph.append(lines[index].strip())
                index += 1
            add("paragraph", " ".join(paragraph))

        warnings = [
            _page_provenance_warning(
                "Markdown input does not contain reliable page coordinates"
            )
        ]
        warnings.extend(quality_warnings(elements))
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            elements=tuple(elements),
            warnings=tuple(warnings),
            page_count=None,
        )
