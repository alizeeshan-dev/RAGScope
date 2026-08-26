"""Optional layout-aware PDF adapter for Docling.

Docling is intentionally an optional dependency.  If it is absent, PDF parsing fails
with ``PARSER_UNAVAILABLE``; the adapter never silently falls back to a layout-losing
PDF text extractor.
"""

from __future__ import annotations

import importlib.metadata
import tempfile
from pathlib import Path
from typing import Any

from backend.app.documents.errors import DocumentParseError, ParserUnavailableError
from backend.app.documents.parsers.base import (
    BoundingBox,
    NormalizedElement,
    ParserConfiguration,
    ParseResult,
    ParseWarning,
)
from backend.app.documents.parsers.quality import quality_warnings

_LABEL_MAP = {
    "title": "title",
    "section_header": "heading",
    "heading": "heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "list_item": "list",
    "list": "list",
    "table": "table",
    "caption": "caption",
    "figure": "figure",
    "picture": "figure",
    "formula": "formula",
}


class DoclingPdfParser:
    parser_id = "docling-pdf"
    media_types = frozenset({"application/pdf"})

    @property
    def parser_version(self) -> str:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def parse(
        self,
        content: bytes,
        configuration: ParserConfiguration | None = None,
    ) -> ParseResult:
        try:
            from docling.document_converter import (  # type: ignore[import-not-found]
                DocumentConverter,
            )
        except ImportError as exc:
            raise ParserUnavailableError(
                "PDF parsing requires the optional 'docling' dependency; install the pdf extra"
            ) from exc

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            converter = DocumentConverter()
            result = converter.convert(temporary_path)
            document = result.document
            elements, extraction_warnings = self._normalize(document)
            page_count = len(getattr(document, "pages", {}) or {}) or None
            warnings = extraction_warnings + quality_warnings(elements, page_count=page_count)
            if self._used_ocr(result):
                warnings.append(
                    ParseWarning(
                        "OCR_USED",
                        "Docling reports OCR-derived content",
                        severity="info",
                    )
                )
            return ParseResult(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                elements=tuple(elements),
                warnings=tuple(warnings),
                page_count=page_count,
                metadata={"layout_aware": True},
            )
        except ParserUnavailableError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                "Docling failed to parse the PDF",
                details={"parser": self.parser_id},
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _normalize(self, document: Any) -> tuple[list[NormalizedElement], list[ParseWarning]]:
        elements: list[NormalizedElement] = []
        warnings: list[ParseWarning] = []
        level_stack: list[tuple[int, str]] = []
        section_stack: list[tuple[int, str]] = []

        for sequence, item_level in enumerate(document.iterate_items()):
            item, level = item_level
            label = str(getattr(item, "label", "other")).lower().split(".")[-1]
            element_type = _LABEL_MAP.get(label, "other")
            text = self._item_text(item, element_type, document)
            if not text.strip() and element_type not in {"figure", "other"}:
                warnings.append(
                    ParseWarning(
                        "MISSING_ELEMENT_TEXT",
                        "A structured Docling element has no serialized text",
                        element_sequence=sequence,
                        metadata={"docling_label": label},
                    )
                )
            while level_stack and level_stack[-1][0] >= level:
                level_stack.pop()
            parent = level_stack[-1][1] if level_stack else None
            local_id = f"element-{sequence}"

            if element_type in {"title", "heading"} and text.strip():
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_stack.append((level, text.strip()))
            section_path = tuple(value for _, value in section_stack)
            page_number, bbox, provenance = self._provenance(item)
            metadata: dict[str, object] = {
                "docling_label": label,
                "hierarchy_level": level,
                "provenance": provenance,
            }
            if element_type == "table":
                metadata["serialization"] = "markdown"
                if not text.strip():
                    warnings.append(
                        ParseWarning(
                            "TABLE_EXTRACTION_FAILED",
                            "Docling identified a table but could not serialize it",
                            page_number=page_number,
                            element_sequence=sequence,
                        )
                    )
            elements.append(
                NormalizedElement(
                    local_id=local_id,
                    parent_local_id=parent,
                    element_type=element_type,  # type: ignore[arg-type]
                    sequence_number=sequence,
                    text=text,
                    page_number=page_number,
                    section_path=section_path,
                    bounding_box=bbox,
                    parser_metadata=metadata,
                )
            )
            level_stack.append((level, local_id))
        return elements, warnings

    @staticmethod
    def _item_text(item: Any, element_type: str, document: Any) -> str:
        if element_type == "table" and hasattr(item, "export_to_markdown"):
            try:
                return str(item.export_to_markdown(doc=document))
            except TypeError:
                try:
                    return str(item.export_to_markdown())
                except Exception:
                    return ""
            except Exception:
                return ""
        return str(getattr(item, "text", "") or "")

    @staticmethod
    def _provenance(
        item: Any,
    ) -> tuple[int | None, BoundingBox | None, list[dict[str, object]]]:
        provenance = getattr(item, "prov", None) or []
        if not provenance:
            return None, None, []
        first = provenance[0]
        page = getattr(first, "page_no", None)
        bbox = getattr(first, "bbox", None)
        if bbox is None:
            return page, None, [
                {"page_number": getattr(value, "page_no", None)} for value in provenance
            ]
        try:
            normalized = BoundingBox(
                left=float(bbox.l),
                top=float(bbox.t),
                right=float(bbox.r),
                bottom=float(bbox.b),
                coordinate_system=str(getattr(bbox, "coord_origin", "docling")),
            )
        except (AttributeError, TypeError, ValueError):
            normalized = None
        serialized: list[dict[str, object]] = []
        for value in provenance:
            value_bbox = getattr(value, "bbox", None)
            item_data: dict[str, object] = {"page_number": getattr(value, "page_no", None)}
            if value_bbox is not None:
                try:
                    item_data["bounding_box"] = {
                        "left": float(value_bbox.l),
                        "top": float(value_bbox.t),
                        "right": float(value_bbox.r),
                        "bottom": float(value_bbox.b),
                        "coordinate_system": str(getattr(value_bbox, "coord_origin", "docling")),
                    }
                except (AttributeError, TypeError, ValueError):
                    item_data["bounding_box_unavailable"] = True
            serialized.append(item_data)
        return page, normalized, serialized

    @staticmethod
    def _used_ocr(result: Any) -> bool:
        # Docling versions expose different conversion metadata.  Only claim OCR
        # usage if an explicit flag/stage is available.
        metadata = getattr(result, "input", None)
        value = str(metadata).lower() if metadata is not None else ""
        return "ocr" in value and any(token in value for token in ("true", "enabled", "used"))
