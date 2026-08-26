"""Narrow provider protocols used by the indexing and future RAG layers.

The domain and persistence layers intentionally depend on these protocols, not on
an SDK such as OpenAI, Anthropic, LangChain, or LlamaIndex.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Generate fixed-width vectors for a batch of texts."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def preprocessing_version(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model_id: str
    provider_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult:
    """Provider response with audit and usage fields kept separate from content."""

    content: str
    raw_response: str
    model_id: str
    provider_id: str
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    latency_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class GenerationProvider(Protocol):
    """Provider-independent plain and JSON-object generation boundary."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult: ...

    def generate_structured(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> StructuredGenerationResult: ...


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    candidate_id: str
    text: str
    original_rank: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankResult:
    candidate_id: str
    original_rank: int
    reranked_rank: int
    score: float


@runtime_checkable
class RerankerProvider(Protocol):
    """Score candidates without destroying their original retrieval ranks."""

    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]: ...
