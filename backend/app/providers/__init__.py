"""Provider-independent model contracts and deterministic local implementations."""

from .base import (
    EmbeddingProvider,
    GenerationProvider,
    GenerationResult,
    RerankCandidate,
    RerankerProvider,
    RerankResult,
    StructuredGenerationResult,
)
from .fake import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
    DeterministicReranker,
)
from .openai_compatible import OpenAICompatibleGenerationProvider

__all__ = [
    "DeterministicEmbeddingProvider",
    "DeterministicGenerationProvider",
    "DeterministicReranker",
    "EmbeddingProvider",
    "GenerationProvider",
    "GenerationResult",
    "OpenAICompatibleGenerationProvider",
    "RerankCandidate",
    "RerankResult",
    "RerankerProvider",
    "StructuredGenerationResult",
]
