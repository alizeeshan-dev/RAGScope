from __future__ import annotations

import hashlib
import re

from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import RetrievalMode

from .schemas import (
    AdaptiveRouteDecision,
    QueryCategory,
    RouterConfiguration,
    RouteReasonCode,
    RouterInput,
)

_WORD_RE = re.compile(r"(?u)\b[\w-]+\b")
_COMPLEX_CATEGORIES = {
    QueryCategory.COMPARISON,
    QueryCategory.MULTI_DOCUMENT_SYNTHESIS,
    QueryCategory.MULTI_HOP_RELATIONSHIP,
    QueryCategory.POTENTIALLY_UNANSWERABLE,
}


class DeterministicAdaptiveRouter:
    """Select a fixed route snapshot without mutating a pipeline configuration."""

    def __init__(self, configuration: RouterConfiguration | None = None) -> None:
        self.configuration = configuration or RouterConfiguration()
        snapshot = self.configuration.model_dump(mode="json")
        self.configuration_hash = hashlib.sha256(canonical_json(snapshot)).hexdigest()

    def route(self, value: RouterInput) -> AdaptiveRouteDecision:
        token_count = len(_WORD_RE.findall(value.query))
        category = value.classification.category

        if (
            category is QueryCategory.DIRECT_FACT
            and token_count <= self.configuration.trivial_query_max_tokens
        ):
            mode = RetrievalMode.NONE
            reason_code = RouteReasonCode.TRIVIAL_QUERY_NO_RETRIEVAL
            reason = "A trivial short query uses the configured no-retrieval route."
            candidate_count = 0
            context_budget = self.configuration.standard_context_budget
            reranking = False
            rewriting = False
        elif (
            category is QueryCategory.METADATA_FILTER
            or bool(value.metadata_filters)
            or value.quoted_phrase_count > 0
            or value.identifier_count > 0
        ):
            mode = RetrievalMode.LEXICAL
            reason_code = RouteReasonCode.METADATA_OR_EXACT_MATCH
            reason = "Structured metadata or exact-match features favor lexical retrieval."
            candidate_count = self.configuration.standard_candidate_count
            context_budget = self.configuration.standard_context_budget
            reranking = False
            rewriting = False
        elif category in _COMPLEX_CATEGORIES:
            mode = RetrievalMode.HYBRID
            reason_code = RouteReasonCode.COMPLEX_EVIDENCE_SEARCH
            reason = "The classified intent requires broad evidence coverage and ranking."
            candidate_count = self.configuration.complex_candidate_count
            context_budget = self.configuration.complex_context_budget
            reranking = self.configuration.enable_complex_reranking
            rewriting = self.configuration.enable_complex_rewriting
        elif category is QueryCategory.BROAD_EXPLORATORY:
            mode = RetrievalMode.DENSE
            reason_code = RouteReasonCode.BROAD_SEMANTIC_SEARCH
            reason = "Broad exploratory intent favors semantic retrieval with a larger context."
            candidate_count = self.configuration.complex_candidate_count
            context_budget = self.configuration.broad_context_budget
            reranking = False
            rewriting = token_count >= self.configuration.rewrite_token_threshold
        else:
            mode = RetrievalMode.DENSE
            reason_code = RouteReasonCode.SEMANTIC_LOOKUP
            reason = "A semantic lookup route was selected for the classified intent."
            candidate_count = self.configuration.standard_candidate_count
            context_budget = self.configuration.standard_context_budget
            reranking = False
            rewriting = token_count >= self.configuration.rewrite_token_threshold

        self._validate_availability(
            value=value,
            mode=mode,
            reranking=reranking,
            rewriting=rewriting,
            candidate_count=candidate_count,
            context_budget=context_budget,
        )
        return AdaptiveRouteDecision(
            retrieval_mode=mode,
            rewriting_enabled=rewriting,
            reranking_enabled=reranking,
            candidate_count=candidate_count,
            context_budget=context_budget,
            reason_code=reason_code,
            reason=reason,
            router_id=self.configuration.router_id,
            router_version=self.configuration.version,
            configuration_hash=self.configuration_hash,
            classifier_version=value.classification.classifier_version,
        )

    def _validate_availability(
        self,
        *,
        value: RouterInput,
        mode: RetrievalMode,
        reranking: bool,
        rewriting: bool,
        candidate_count: int,
        context_budget: int,
    ) -> None:
        capabilities = value.capabilities
        unavailable: list[str] = []
        if mode not in self.configuration.allowed_retrieval_modes:
            unavailable.append(
                f"retrieval mode '{mode.value}' is not allowed by router configuration"
            )
        if not capabilities.supports(mode):
            unavailable.append(f"retrieval mode '{mode.value}'")
        if reranking and not capabilities.reranker_available:
            unavailable.append("reranker")
        if rewriting and not capabilities.rewriting_available:
            unavailable.append("query rewriting")
        if candidate_count > capabilities.maximum_candidate_count:
            maximum = capabilities.maximum_candidate_count
            unavailable.append(
                f"candidate count {candidate_count} (maximum {maximum})"
            )
        if context_budget > capabilities.maximum_context_budget:
            unavailable.append(
                f"context budget {context_budget} (maximum {capabilities.maximum_context_budget})"
            )
        if unavailable:
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Adaptive route requires unavailable capability: " + ", ".join(unavailable) + ".",
            )
