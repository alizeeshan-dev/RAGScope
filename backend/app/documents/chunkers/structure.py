"""Section-aware chunker that keeps coherent elements and tables intact."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.documents.chunkers.base import (
    GeneratedChunk,
    Tokenizer,
    common_section_path,
    pages,
    stable_chunk_hash,
    unique_source_ids,
)
from backend.app.documents.parsers.base import NormalizedElement


@dataclass(frozen=True, slots=True)
class StructureAwareConfiguration:
    target_tokens: int = 384
    include_section_titles: bool = True
    preserve_tables: bool = True

    def __post_init__(self) -> None:
        if self.target_tokens < 1:
            raise ValueError("target_tokens must be positive")

    def as_dict(self) -> dict[str, object]:
        return {
            "target_tokens": self.target_tokens,
            "include_section_titles": self.include_section_titles,
            "preserve_tables": self.preserve_tables,
            "tokenizer": Tokenizer.tokenizer_id,
        }


class StructureAwareChunker:
    chunker_id = "structure-aware-v1"

    def __init__(self, configuration: StructureAwareConfiguration | None = None) -> None:
        self.configuration = configuration or StructureAwareConfiguration()
        self.tokenizer = Tokenizer()

    def chunk(self, elements: list[NormalizedElement]) -> list[GeneratedChunk]:
        ordered = [
            element
            for element in sorted(elements, key=lambda item: item.sequence_number)
            if element.text.strip()
        ]
        groups: list[list[NormalizedElement]] = []
        current: list[NormalizedElement] = []
        current_tokens = 0
        current_section: tuple[str, ...] | None = None

        def flush() -> None:
            nonlocal current, current_tokens
            if current:
                groups.append(current)
            current = []
            current_tokens = 0

        for element in ordered:
            count = self.tokenizer.count(element.text)
            section_changed = (
                current_section is not None and element.section_path != current_section
            )
            is_table = element.element_type == "table"
            if section_changed or (is_table and self.configuration.preserve_tables):
                flush()
            splittable_oversize = count > self.configuration.target_tokens and not (
                is_table and self.configuration.preserve_tables
            )
            if splittable_oversize:
                flush()
                groups.extend(self._split_large_element(element))
                current_section = element.section_path
                continue
            if current and current_tokens + count > self.configuration.target_tokens:
                flush()
            current.append(element)
            current_tokens += count
            current_section = element.section_path
            if is_table and self.configuration.preserve_tables:
                flush()
        flush()
        return [self._build_chunk(sequence, group) for sequence, group in enumerate(groups)]

    def _split_large_element(self, element: NormalizedElement) -> list[list[NormalizedElement]]:
        tokens = self.tokenizer.tokenize(element.text)
        pieces: list[list[NormalizedElement]] = []
        for offset in range(0, len(tokens), self.configuration.target_tokens):
            text = self.tokenizer.join(tokens[offset : offset + self.configuration.target_tokens])
            pieces.append(
                [
                    NormalizedElement(
                        local_id=element.local_id,
                        parent_local_id=element.parent_local_id,
                        element_type=element.element_type,
                        sequence_number=element.sequence_number,
                        text=text,
                        page_number=element.page_number,
                        section_path=element.section_path,
                        bounding_box=element.bounding_box,
                        parser_metadata={**element.parser_metadata, "structure_split": True},
                    )
                ]
            )
        return pieces

    def _build_chunk(self, sequence: int, elements: list[NormalizedElement]) -> GeneratedChunk:
        section_path = common_section_path(elements)
        body = "\n\n".join(element.text.strip() for element in elements)
        if self.configuration.include_section_titles and section_path:
            heading = " > ".join(section_path)
            if not body.startswith(heading):
                body = f"{heading}\n\n{body}"
        token_count = self.tokenizer.count(body)
        page_start, page_end = pages(elements)
        configuration = self.configuration.as_dict()
        contains_table = any(element.element_type == "table" for element in elements)
        return GeneratedChunk(
            sequence_number=sequence,
            text=body,
            token_count=token_count,
            page_start=page_start,
            page_end=page_end,
            section_path=section_path,
            source_element_ids=unique_source_ids(elements),
            content_hash=stable_chunk_hash(
                chunker_id=self.chunker_id,
                configuration=configuration,
                sequence_number=sequence,
                text=body,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
            ),
            metadata={
                "configuration": configuration,
                "tokenizer": self.tokenizer.tokenizer_id,
                "contains_table": contains_table,
                "table_serializations": [
                    element.parser_metadata.get("serialization", "unknown")
                    for element in elements
                    if element.element_type == "table"
                ],
                "oversize_coherent_unit": token_count > self.configuration.target_tokens,
            },
        )
