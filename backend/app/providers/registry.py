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
from .gemini import GeminiEmbeddingProvider, GeminiGenerationProvider
from .local_cross_encoder import SentenceTransformersCrossEncoderReranker
from .openai_compatible import OpenAICompatibleGenerationProvider


class UnsupportedProviderError(ValueError):
    pass


def create_embedding_provider(
    configuration: Mapping[str, Any], *, settings: Settings | None = None
) -> EmbeddingProvider:
    provider = str(configuration.get("provider", "fake"))
    if provider == "fake":
        return DeterministicEmbeddingProvider(
            dimension=int(configuration.get("dimension", 64)),
            model_id=str(configuration.get("model", "fake-hash-embedding-v1")),
        )
    if provider == "gemini":
        resolved_settings = settings or get_settings()
        return GeminiEmbeddingProvider.from_settings(
            resolved_settings,
            model_id=str(
                configuration.get("model", resolved_settings.gemini_embedding_model)
            ),
            dimension=int(
                configuration.get("dimension", resolved_settings.gemini_embedding_dimension)
            ),
            timeout_seconds=float(
                configuration.get(
                    "timeout_seconds", resolved_settings.embedding_timeout_seconds
                )
            ),
            batch_size=int(
                configuration.get(
                    "batch_size", resolved_settings.gemini_embedding_batch_size
                )
            ),
            task_type=str(
                configuration.get("task_type", resolved_settings.gemini_embedding_task_type)
            ),
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
                else resolved_settings.generation_input_price_per_million
            ),
            output_price_per_million=(
                float(configuration["output_price_per_million_tokens"])
                if configuration.get("output_price_per_million_tokens") is not None
                else resolved_settings.generation_output_price_per_million
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
                else resolved_settings.generation_input_price_per_million
            ),
            output_price_per_million=(
                float(configuration["output_price_per_million_tokens"])
                if configuration.get("output_price_per_million_tokens") is not None
                else resolved_settings.generation_output_price_per_million
            ),
        )
    raise UnsupportedProviderError(f"generation provider is not configured: {provider}")


def create_reranker_provider(configuration: Mapping[str, Any]) -> RerankerProvider:
    provider = str(configuration.get("provider", "fake"))
    if provider == "fake":
        return DeterministicReranker()
    if provider in {
        "sentence_transformers_cross_encoder",
        "sentence-transformers-cross-encoder",
    }:
        return SentenceTransformersCrossEncoderReranker(
            model_id=str(configuration.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")),
            batch_size=int(configuration.get("batch_size", 16)),
            device=(
                str(configuration["device"])
                if configuration.get("device") is not None
                else None
            ),
            local_files_only=bool(configuration.get("local_files_only", True)),
            revision=(
                str(configuration["model_revision"])
                if configuration.get("model_revision") is not None
                else None
            ),
        )
    raise UnsupportedProviderError(f"reranker provider is not configured: {provider}")
