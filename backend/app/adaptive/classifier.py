from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from backend.app.corpora.hashing import canonical_json

from .schemas import (
    ClassifierConfiguration,
    ConfidenceLabel,
    QueryCategory,
    QueryClassification,
)

_WORD_RE = re.compile(r"(?u)\b[\w-]+\b")


class DeterministicQueryClassifier:
    """Classify observable query text with replayable, provider-independent rules."""

    def __init__(self, configuration: ClassifierConfiguration | None = None) -> None:
        self.configuration = configuration or ClassifierConfiguration()
        snapshot = self.configuration.model_dump(mode="json")
        self.configuration_hash = hashlib.sha256(canonical_json(snapshot)).hexdigest()

    def classify(
        self, query: str, *, extracted_metadata: Mapping[str, object] | None = None
    ) -> QueryClassification:
        lowered = query.casefold()
        metadata = extracted_metadata or {}
        token_count = len(_WORD_RE.findall(query))

        if any(term in lowered for term in ("false premise", "prove that", "definitely never")):
            category = QueryCategory.POTENTIALLY_UNANSWERABLE
            code = "PREMISE_OR_ABSOLUTIST_TERMS"
            reason = "The query contains premise-sensitive or absolutist language."
            confidence = ConfidenceLabel.HIGH
        elif any(term in lowered for term in ("compare", "difference", "versus", " vs ")):
            category = QueryCategory.COMPARISON
            code = "COMPARISON_TERMS"
            reason = "The query explicitly requests a comparison."
            confidence = ConfidenceLabel.HIGH
        elif any(term in lowered for term in ("across papers", "across studies", "synthesize")):
            category = QueryCategory.MULTI_DOCUMENT_SYNTHESIS
            code = "CROSS_DOCUMENT_TERMS"
            reason = "The query explicitly requests evidence across documents."
            confidence = ConfidenceLabel.HIGH
        elif any(
            term in lowered
            for term in ("relationship", "how does", "leads to", "depends on", "through which")
        ):
            category = QueryCategory.MULTI_HOP_RELATIONSHIP
            code = "RELATIONSHIP_TERMS"
            reason = "The query asks for a relationship that may require linked evidence."
            confidence = ConfidenceLabel.MEDIUM
        elif metadata.get("publication_years") or any(
            term in lowered for term in ("published", "license", "author", "language:")
        ):
            category = QueryCategory.METADATA_FILTER
            code = "METADATA_TERMS"
            reason = "The query contains structured metadata constraints."
            confidence = ConfidenceLabel.HIGH
        elif any(term in lowered for term in ("dataset", "corpus", "benchmark")):
            category = QueryCategory.DATASET_LOOKUP
            code = "DATASET_TERMS"
            reason = "The query asks for dataset or benchmark information."
            confidence = ConfidenceLabel.HIGH
        elif any(term in lowered for term in ("overview", "explore", "broadly", "what is known")):
            category = QueryCategory.BROAD_EXPLORATORY
            code = "EXPLORATORY_TERMS"
            reason = "The query requests broad or exploratory coverage."
            confidence = ConfidenceLabel.HIGH
        elif token_count >= self.configuration.broad_query_token_threshold:
            category = QueryCategory.BROAD_EXPLORATORY
            code = "LONG_OPEN_QUERY"
            reason = "The query is long and has no narrower deterministic intent marker."
            confidence = ConfidenceLabel.LOW
        else:
            category = QueryCategory.DIRECT_FACT
            code = "DEFAULT_FACT_QUESTION"
            reason = "No complex or structured intent marker was detected."
            confidence = ConfidenceLabel.MEDIUM

        return QueryClassification(
            category=category,
            reason_code=code,
            reason=reason,
            confidence=confidence,
            classifier_id=self.configuration.classifier_id,
            classifier_version=self.configuration.version,
            configuration_hash=self.configuration_hash,
        )
