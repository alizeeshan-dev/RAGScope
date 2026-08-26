"""Fixed-token sliding-window chunker with element/page provenance."""

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
class FixedTokenConfiguration:
    target_tokens: int = 256
    overlap_tokens: int = 32
    include_section_titles: bool = False

    def __post_init__(self) -> None:
        if self.target_tokens < 1:
            raise ValueError("target_tokens must be positive")
        if self.overlap_tokens < 0 or self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")

    def as_dict(self) -> dict[str, object]:
        return {
            "target_tokens": self.target_tokens,
            "overlap_tokens": self.overlap_tokens,
            "include_section_titles": self.include_section_titles,
            "tokenizer": Tokenizer.tokenizer_id,
        }


class FixedTokenChunker:
    chunker_id = "fixed-token-v1"

    def __init__(self, configuration: FixedTokenConfiguration | None = None) -> None:
        self.configuration = configuration or FixedTokenConfiguration()
        self.tokenizer = Tokenizer()

    def chunk(self, elements: list[NormalizedElement]) -> list[GeneratedChunk]:
        # Each token carries its source element. Section titles are injected at section
        # transitions and attributed to the following element.
        stream: list[tuple[str, NormalizedElement]] = []
        last_section: tuple[str, ...] = ()
        for element in sorted(elements, key=lambda item: item.sequence_number):
            include_heading = (
                self.configuration.include_section_titles
                and element.section_path
                and element.section_path != last_section
            )
            if include_heading:
                prefix = " > ".join(element.section_path)
                stream.extend((token, element) for token in self.tokenizer.tokenize(prefix))
            stream.extend((token, element) for token in self.tokenizer.tokenize(element.text))
            last_section = element.section_path
        if not stream:
            return []

        chunks: list[GeneratedChunk] = []
        start = 0
        step = self.configuration.target_tokens - self.configuration.overlap_tokens
        while start < len(stream):
            window = stream[start : start + self.configuration.target_tokens]
            tokens = [token for token, _ in window]
            source_elements = list(dict.fromkeys(element.local_id for _, element in window))
            by_id = {element.local_id: element for _, element in window}
            used = [by_id[element_id] for element_id in source_elements]
            text = self.tokenizer.join(tokens)
            page_start, page_end = pages(used)
            section_path = common_section_path(used)
            sequence = len(chunks)
            configuration = self.configuration.as_dict()
            chunks.append(
                GeneratedChunk(
                    sequence_number=sequence,
                    text=text,
                    token_count=len(tokens),
                    page_start=page_start,
                    page_end=page_end,
                    section_path=section_path,
                    source_element_ids=unique_source_ids(used),
                    content_hash=stable_chunk_hash(
                        chunker_id=self.chunker_id,
                        configuration=configuration,
                        sequence_number=sequence,
                        text=text,
                        page_start=page_start,
                        page_end=page_end,
                        section_path=section_path,
                    ),
                    metadata={
                        "configuration": configuration,
                        "tokenizer": self.tokenizer.tokenizer_id,
                        "contains_table": any(element.element_type == "table" for element in used),
                    },
                )
            )
            if start + self.configuration.target_tokens >= len(stream):
                break
            start += step
        return chunks
