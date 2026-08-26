from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from backend.app.adaptive.classifier import DeterministicQueryClassifier
from backend.app.core.errors import DomainError
from backend.app.pipelines.schemas import QueryProcessingConfiguration

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_WORD_RE = re.compile(r"(?u)\b[\w-]+\b")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "does",
    "for",
    "how",
    "in",
    "is",
    "of",
    "the",
    "to",
    "what",
    "which",
    "who",
}


@dataclass(frozen=True, slots=True)
class ProcessedQuery:
    original_query: str
    normalized_query: str
    rewritten_query: str | None
    classification: dict[str, object]
    extracted_metadata: dict[str, object] = field(default_factory=dict)


class QueryProcessor:
    def __init__(self, configuration: QueryProcessingConfiguration) -> None:
        self.configuration = configuration

    def process(self, query: str) -> ProcessedQuery:
        normalized = self.normalize(query)
        metadata = self.extract_metadata(normalized)
        classification = self.classify(normalized, metadata)
        rewritten = self.rewrite(normalized)
        return ProcessedQuery(query, normalized, rewritten, classification, metadata)

    @staticmethod
    def normalize(query: str) -> str:
        if not isinstance(query, str):
            raise DomainError("INVALID_QUERY", "Query text must be a string.", status_code=422)
        normalized = " ".join(unicodedata.normalize("NFKC", query).split())
        if not normalized:
            raise DomainError("INVALID_QUERY", "Query text cannot be empty.", status_code=422)
        if len(normalized) > 10_000:
            raise DomainError(
                "INVALID_QUERY", "Query text cannot exceed 10,000 characters.", status_code=422
            )
        return normalized

    def classify(self, query: str, metadata: dict[str, object]) -> dict[str, object]:
        if self.configuration.classification_enabled:
            return DeterministicQueryClassifier().classify(
                query, extracted_metadata=metadata
            ).model_dump(mode="json")
        return {
            "category": "bypassed",
            "reason_code": "CLASSIFICATION_DISABLED",
            "reason": "classification_disabled_by_pipeline",
            "confidence": "not_applicable",
            "classifier_id": "rule-query-classifier",
            "classifier_version": "1.0.0",
            "configuration_hash": None,
        }

    def rewrite(self, query: str) -> str | None:
        return self._rewrite(query) if self.configuration.rewriting_enabled else None

    @classmethod
    def extract_metadata(cls, query: str) -> dict[str, object]:
        return cls._extract_metadata(query)

    def _rewrite(self, query: str) -> str:
        if self.configuration.rewriting_strategy == "normalize-only":
            return query
        words = [word for word in _WORD_RE.findall(query.casefold()) if word not in _STOPWORDS]
        return " ".join(dict.fromkeys(words)) or query

    @staticmethod
    def _extract_metadata(query: str) -> dict[str, object]:
        years = sorted({int(value) for value in _YEAR_RE.findall(query)})
        acronyms = sorted(set(re.findall(r"\b[A-Z][A-Z0-9-]{1,12}\b", query)))
        quoted_entities = re.findall(r'["“]([^"”]{1,200})["”]', query)
        return {
            "publication_years": years,
            "entities": list(dict.fromkeys([*quoted_entities, *acronyms])),
        }

    @staticmethod
    def _classify(query: str, metadata: dict[str, object]) -> dict[str, object]:
        lowered = query.casefold()
        category = "direct_fact"
        reason = "default_fact_question"
        confidence = "medium"
        if any(term in lowered for term in ("compare", "difference", "versus", " vs ")):
            category, reason, confidence = "comparison", "comparison_terms", "high"
        elif any(term in lowered for term in ("across papers", "across studies", "synthesize")):
            category, reason, confidence = (
                "multi_document_synthesis",
                "cross_document_terms",
                "high",
            )
        elif any(
            term in lowered for term in ("relationship", "how does", "leads to", "depends on")
        ):
            category, reason = "multi_hop", "relationship_terms"
        elif any(term in lowered for term in ("dataset", "corpus", "benchmark")):
            category, reason = "dataset_lookup", "dataset_terms"
        elif metadata.get("publication_years") or any(
            term in lowered for term in ("published", "license", "author")
        ):
            category, reason = "metadata_filter", "metadata_terms"
        elif any(term in lowered for term in ("overview", "explore", "broadly", "what is known")):
            category, reason = "broad_exploratory", "exploratory_terms"
        elif any(term in lowered for term in ("false premise", "prove that", "definitely never")):
            category, reason = "potentially_unanswerable", "premise_or_absolutist_terms"
        return {"category": category, "reason": reason, "confidence": confidence}
