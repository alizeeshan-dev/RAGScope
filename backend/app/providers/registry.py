"""Explicit provider construction from serializable configuration snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.app.core.config import Settings, get_settings

from .base import EmbeddingProvider, GenerationProvider, RerankerProvider
from .fake import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
    DeterministicReranker,
)
from .gemini import GeminiGenerationProvider
from .openai_compatible import OpenAICompatibleGenerationProvider


class UnsupportedProviderError(ValueError):
    pass


def create_embedding_provider(configuration: Mapping[str, Any]) -> EmbeddingProvider:
    provider = str(configuration.get("provider", "fake"))
    if provider == "fake":
        return DeterministicEmbeddingProvider(
            dimension=int(configuration.get("dimension", 64)),
            model_id=str(configuration.get("model", "fake-hash-embedding-v1")),
        )
    raise UnsupportedProviderError(f"embedding provider is not configured: {provider}")


def create_generation_provider(
    configuration: Mapping[str, Any], *, settings: Settings | None = None
) -> GenerationProvider:
    provider = str(configuration.get("provider", "fake"))
    if provider == "fake":
        return DeterministicGenerationProvider(
            model_id=str(configuration.get("model", "fake-generation-v1"))
        )
    if provider == "gemini":
        resolved_settings = settings or get_settings()
        return GeminiGenerationProvider.from_settings(
            resolved_settings,
            model_id=str(configuration.get("model", resolved_settings.gemini_model)),
            timeout_seconds=float(
                configuration.get(
                    "timeout_seconds", resolved_settings.generation_timeout_seconds
                )
            ),
            input_price_per_million=(
                float(configuration["input_price_per_million_tokens"])
                if configuration.get("input_price_per_million_tokens") is not None
                else None
            ),
            output_price_per_million=(
                float(configuration["output_price_per_million_tokens"])
                if configuration.get("output_price_per_million_tokens") is not None
                else None
            ),
        )
    if provider in {"openai_compatible", "openai-compatible"}:
        resolved_settings = settings or get_settings()
        return OpenAICompatibleGenerationProvider.from_settings(
            resolved_settings,
            model_id=str(configuration.get("model", resolved_settings.generation_model)),
            timeout_seconds=float(
                configuration.get(
                    "timeout_seconds", resolved_settings.generation_timeout_seconds
                )
            ),
            input_price_per_million=(
                float(configuration["input_price_per_million_tokens"])
                if configuration.get("input_price_per_million_tokens") is not None
                else None
            ),
            output_price_per_million=(
                float(configuration["output_price_per_million_tokens"])
                if configuration.get("output_price_per_million_tokens") is not None
                else None
            ),
        )
    raise UnsupportedProviderError(f"generation provider is not configured: {provider}")


def create_reranker_provider(configuration: Mapping[str, Any]) -> RerankerProvider:
    provider = str(configuration.get("provider", "fake"))
    if provider == "fake":
        return DeterministicReranker()
    raise UnsupportedProviderError(f"reranker provider is not configured: {provider}")
