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
from .gemini import GeminiEmbeddingProvider, GeminiGenerationProvider
from .local_cross_encoder import SentenceTransformersCrossEncoderReranker
from .openai_compatible import OpenAICompatibleGenerationProvider

__all__ = [
    "DeterministicEmbeddingProvider",
    "DeterministicGenerationProvider",
    "DeterministicReranker",
    "EmbeddingProvider",
    "GenerationProvider",
    "GenerationResult",
    "GeminiEmbeddingProvider",
    "GeminiGenerationProvider",
    "OpenAICompatibleGenerationProvider",
    "RerankCandidate",
    "RerankResult",
    "RerankerProvider",
    "SentenceTransformersCrossEncoderReranker",
    "StructuredGenerationResult",
]
