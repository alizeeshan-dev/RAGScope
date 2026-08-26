from __future__ import annotations

import math

import pytest
from backend.app.providers.base import RerankCandidate
from backend.app.providers.fake import (
    DeterministicEmbeddingProvider,
    DeterministicGenerationProvider,
    DeterministicReranker,
)


def test_fake_embeddings_are_deterministic_normalized_and_batched() -> None:
    provider = DeterministicEmbeddingProvider(dimension=32)
    first = provider.embed(["alpha beta beta", ""])
    second = provider.embed(["alpha beta beta", ""])

    assert first == second
    assert len(first) == 2
    assert len(first[0]) == 32
    assert math.sqrt(sum(value * value for value in first[0])) == pytest.approx(1.0)
    assert first[1] == [0.0] * 32


def test_fake_generation_rejects_nondeterministic_temperature() -> None:
    provider = DeterministicGenerationProvider()
    assert provider.generate("same input") == provider.generate("same input")
    with pytest.raises(ValueError, match="temperature=0"):
        provider.generate("input", temperature=0.5)


def test_fake_reranker_preserves_original_ranks() -> None:
    results = DeterministicReranker().rerank(
        "climate dataset",
        [
            RerankCandidate("a", "unrelated prose", original_rank=1),
            RerankCandidate("b", "climate dataset metadata", original_rank=2),
        ],
    )
    assert [result.candidate_id for result in results] == ["b", "a"]
    assert [result.original_rank for result in results] == [2, 1]
    assert [result.reranked_rank for result in results] == [1, 2]
