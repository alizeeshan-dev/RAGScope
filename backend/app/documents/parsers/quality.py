"""Conservative parser-quality heuristics.

These warnings describe signals that merit inspection; they are intentionally not
presented as proof that parsing is wrong.
"""

from __future__ import annotations

import re
from collections import Counter

from backend.app.documents.parsers.base import NormalizedElement, ParseWarning

_REPLACEMENT = "\ufffd"
_BROKEN_WORD = re.compile(r"\b[A-Za-z]{2,}-\s+[a-z]{2,}\b")


def quality_warnings(
    elements: list[NormalizedElement],
    *,
    page_count: int | None = None,
) -> list[ParseWarning]:
    warnings: list[ParseWarning] = []
    if not elements or not any(element.text.strip() for element in elements):
        warnings.append(ParseWarning("MISSING_DOCUMENT_TEXT", "Parser returned no document text"))
        return warnings

    if page_count:
        pages_with_text = {
            element.page_number
            for element in elements
            if element.page_number and element.text.strip()
        }
        for page in range(1, page_count + 1):
            if page not in pages_with_text:
                warnings.append(
                    ParseWarning(
                        "EMPTY_OR_MISSING_PAGE_TEXT",
                        "No extracted text was found for this page",
                        page_number=page,
                    )
                )

    edge_text: Counter[str] = Counter()
    for element in elements:
        text = " ".join(element.text.split())
        if not text:
            continue
        if _REPLACEMENT in text:
            warnings.append(
                ParseWarning(
                    "SUSPECT_ENCODING",
                    "Unicode replacement characters were found",
                    element_sequence=element.sequence_number,
                )
            )
        if _BROKEN_WORD.search(text):
            warnings.append(
                ParseWarning(
                    "POSSIBLE_BROKEN_WORD",
                    "A line-break hyphen may have split a word",
                    element_sequence=element.sequence_number,
                )
            )
        if len(text) > 20_000:
            warnings.append(
                ParseWarning(
                    "UNUSUALLY_LONG_ELEMENT",
                    "Extracted element is unusually long",
                    element_sequence=element.sequence_number,
                    metadata={"character_count": len(text)},
                )
            )
        if element.page_number and len(text) <= 120:
            edge_text[text] += 1

    repeated_threshold = max(3, ((page_count or 0) + 1) // 2)
    for text, count in edge_text.items():
        if count >= repeated_threshold:
            warnings.append(
                ParseWarning(
                    "POSSIBLE_REPEATED_HEADER_FOOTER",
                    "Short text repeats across multiple pages",
                    metadata={"occurrences": count, "text_preview": text[:120]},
                )
            )
    page_sequence = [element.page_number for element in elements if element.page_number is not None]
    out_of_order = any(
        current < previous
        for previous, current in zip(page_sequence, page_sequence[1:], strict=False)
    )
    if out_of_order:
        warnings.append(
            ParseWarning(
                "SUSPICIOUS_READING_ORDER",
                "Element page numbers move backwards in parser order; inspect layout ordering",
            )
        )

    for index, element in enumerate(elements):
        if element.element_type not in {"title", "heading"}:
            continue
        following = elements[index + 1 :]
        section_text = []
        for candidate in following:
            is_peer_heading = candidate.element_type in {"title", "heading"} and len(
                candidate.section_path
            ) <= len(element.section_path)
            if is_peer_heading:
                break
            section_text.append(candidate.text)
        character_count = sum(len(value.strip()) for value in section_text)
        if character_count < 20:
            warnings.append(
                ParseWarning(
                    "UNUSUALLY_SHORT_SECTION",
                    "A section heading has little or no extracted body text",
                    element_sequence=element.sequence_number,
                    metadata={"body_character_count": character_count},
                )
            )
    return warnings
