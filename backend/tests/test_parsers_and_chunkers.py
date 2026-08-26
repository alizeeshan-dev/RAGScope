from __future__ import annotations

import importlib.util

import pytest
from backend.app.documents.chunkers import (
    FixedTokenChunker,
    FixedTokenConfiguration,
    StructureAwareChunker,
    StructureAwareConfiguration,
)
from backend.app.documents.errors import ParserUnavailableError
from backend.app.documents.parsers import DoclingPdfParser, MarkdownParser, PlainTextParser

MARKDOWN = b"""# A Study

Abstract text describes the investigation and its result.

## Measurements

- first condition
- second condition

| Metric | Value |
| --- | --- |
| Accuracy | 0.91 |

The concluding paragraph reports the final observation.
"""


def test_markdown_parser_preserves_hierarchy_tables_and_honest_provenance() -> None:
    first = MarkdownParser().parse(MARKDOWN)
    second = MarkdownParser().parse(MARKDOWN)

    assert first == second
    assert [element.sequence_number for element in first.elements] == list(
        range(len(first.elements))
    )
    assert first.elements[0].element_type == "title"
    table = next(element for element in first.elements if element.element_type == "table")
    assert table.parser_metadata["serialization"] == "markdown"
    assert table.page_number is None
    assert table.section_path == ("A Study", "Measurements")
    assert any(warning.code == "PAGE_PROVENANCE_UNAVAILABLE" for warning in first.warnings)


def test_plain_text_parser_uses_paragraphs_without_inventing_pages() -> None:
    result = PlainTextParser().parse(b"First paragraph.\n\nSecond paragraph.")
    assert len(result.elements) == 2
    assert all(element.page_number is None for element in result.elements)


def test_fixed_chunker_is_deterministic_and_budgeted() -> None:
    elements = list(MarkdownParser().parse(MARKDOWN).elements)
    chunker = FixedTokenChunker(FixedTokenConfiguration(target_tokens=12, overlap_tokens=3))
    first = chunker.chunk(elements)
    second = chunker.chunk(elements)

    assert first == second
    assert all(chunk.token_count <= 12 for chunk in first)
    assert all(chunk.source_element_ids for chunk in first)
    assert [chunk.sequence_number for chunk in first] == list(range(len(first)))
    assert len({chunk.content_hash for chunk in first}) == len(first)


def test_structure_chunker_differs_and_keeps_table_coherent() -> None:
    elements = list(MarkdownParser().parse(MARKDOWN).elements)
    fixed = FixedTokenChunker(
        FixedTokenConfiguration(target_tokens=10, overlap_tokens=0)
    ).chunk(elements)
    structured = StructureAwareChunker(
        StructureAwareConfiguration(target_tokens=10, include_section_titles=False)
    ).chunk(elements)

    assert [chunk.text for chunk in fixed] != [chunk.text for chunk in structured]
    table_id = next(element.local_id for element in elements if element.element_type == "table")
    table_chunks = [chunk for chunk in structured if table_id in chunk.source_element_ids]
    assert len(table_chunks) == 1
    assert table_chunks[0].metadata["contains_table"] is True
    assert table_chunks[0].metadata["oversize_coherent_unit"] is True


def test_docling_unavailable_is_explicit_in_base_install() -> None:
    if importlib.util.find_spec("docling") is not None:
        pytest.skip("Docling is installed; real-PDF behavior belongs to optional integration tests")
    with pytest.raises(ParserUnavailableError, match="optional 'docling'"):
        DoclingPdfParser().parse(b"%PDF-1.4\n%%EOF")
