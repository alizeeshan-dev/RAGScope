"""Parser protocol and built-in document parsers."""

from backend.app.documents.parsers.base import (
    BoundingBox,
    NormalizedElement,
    Parser,
    ParserConfiguration,
    ParseResult,
    ParseWarning,
)
from backend.app.documents.parsers.docling_pdf import DoclingPdfParser
from backend.app.documents.parsers.text import MarkdownParser, PlainTextParser

__all__ = [
    "BoundingBox",
    "DoclingPdfParser",
    "MarkdownParser",
    "NormalizedElement",
    "ParseResult",
    "ParseWarning",
    "Parser",
    "ParserConfiguration",
    "PlainTextParser",
]
