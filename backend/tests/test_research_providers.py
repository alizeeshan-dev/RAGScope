from __future__ import annotations

import importlib
import json
import math

import httpx
import pytest
from backend.app.core.config import Settings
from backend.app.providers.base import RerankCandidate
from backend.app.providers.gemini import (
    GeminiEmbeddingProvider,
    GeminiEmbeddingProviderError,
)
from backend.app.providers.local_cross_encoder import (
    LocalCrossEncoderError,
    SentenceTransformersCrossEncoderReranker,
)
from backend.app.providers.registry import (
    create_embedding_provider,
    create_reranker_provider,
)


def test_gemini_embedding_uses_frozen_native_contract_and_normalizes() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "url": str(request.url),
                "key": request.headers["x-goog-api-key"],
                "payload": json.loads(request.content),
            }
        )
        payload = requests[-1]["payload"]
        assert isinstance(payload, dict)
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [3.0, 4.0, 0.0]}
                    for _request in payload["requests"]
                ]
            },
        )

    provider = GeminiEmbeddingProvider(
        model_id="models/gemini-embedding-001",
        dimension=3,
        base_url="https://example.test",
        api_key="never-persist-this-key",
        timeout_seconds=10,
        batch_size=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    vectors = provider.embed(["alpha", "beta", "gamma"])

    assert len(requests) == 2
    assert requests[0]["url"] == (
        "https://example.test/v1beta/models/gemini-embedding-001:batchEmbedContents"
    )
    assert requests[0]["key"] == "never-persist-this-key"
    first_payload = requests[0]["payload"]
    assert isinstance(first_payload, dict)
    first_request = first_payload["requests"][0]
    assert first_request == {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": "alpha"}]},
        "taskType": "SEMANTIC_SIMILARITY",
        "outputDimensionality": 3,
    }
    assert vectors == [[0.6, 0.8, 0.0]] * 3
    assert all(math.isclose(sum(value * value for value in vector), 1.0) for vector in vectors)
    assert provider.preprocessing_version == "gemini-semantic-similarity-l2-v1"
    assert "never-persist-this-key" not in repr(provider.preprocessing_version)


def test_gemini_embedding_registry_is_opt_in_and_requires_secret() -> None:
    with pytest.raises(ValueError, match="RAGSCOPE_GEMINI_API_KEY"):
        create_embedding_provider(
            {
                "provider": "gemini",
                "model": "gemini-embedding-001",
                "dimension": 768,
            },
            settings=Settings(_env_file=None, gemini_api_key=None),
        )

    provider = create_embedding_provider(
        {"provider": "gemini", "model": "gemini-embedding-001", "dimension": 256},
        settings=Settings(
            _env_file=None,
            gemini_api_key="test-only-key",
            gemini_base_url="https://example.test",
        ),
    )
    assert isinstance(provider, GeminiEmbeddingProvider)
    assert provider.model_id == "gemini-embedding-001"
    assert provider.dimension == 256


def test_gemini_embedding_failure_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="response contains never-log-key and source text")

    provider = GeminiEmbeddingProvider(
        model_id="gemini-embedding-001",
        dimension=3,
        base_url="https://example.test",
        api_key="never-log-key",
        timeout_seconds=10,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(GeminiEmbeddingProviderError) as raised:
        provider.embed(["sensitive source text"])
    assert "never-log-key" not in str(raised.value)
    assert "sensitive source text" not in str(raised.value)


class _StaticCrossEncoder:
    def __init__(self, scores: list[float] | Exception) -> None:
        self.scores = scores
        self.calls: list[tuple[list[tuple[str, str]], int]] = []

    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
    ) -> object:
        assert show_progress_bar is False
        assert convert_to_numpy is False
        self.calls.append((sentences, batch_size))
        if isinstance(self.scores, Exception):
            raise self.scores
        return self.scores


def test_cross_encoder_reranks_deterministically_and_preserves_original_rank() -> None:
    model = _StaticCrossEncoder([0.2, 0.9, 0.9])
    provider = SentenceTransformersCrossEncoderReranker(
        model_id="local/frozen-cross-encoder",
        batch_size=2,
        model=model,
    )
    results = provider.rerank(
        "climate dataset",
        [
            RerankCandidate("a", "first", 1),
            RerankCandidate("b", "second", 2),
            RerankCandidate("c", "third", 3),
        ],
        top_k=2,
    )

    observed = [
        (result.candidate_id, result.original_rank, result.reranked_rank)
        for result in results
    ]
    assert observed == [
        ("b", 2, 1),
        ("c", 3, 2),
    ]
    assert model.calls == [
        (
            [
                ("climate dataset", "first"),
                ("climate dataset", "second"),
                ("climate dataset", "third"),
            ],
            2,
        )
    ]


def test_cross_encoder_registry_does_not_import_or_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(_name: str, _package: str | None = None) -> object:
        raise AssertionError("provider construction must stay lazy")

    monkeypatch.setattr(importlib, "import_module", fail_import)
    provider = create_reranker_provider(
        {
            "provider": "sentence_transformers_cross_encoder",
            "model": "local/frozen-cross-encoder",
            "local_files_only": True,
        }
    )
    assert isinstance(provider, SentenceTransformersCrossEncoderReranker)
    assert provider.model_id == "local/frozen-cross-encoder"


def test_cross_encoder_failure_does_not_copy_model_or_input_details() -> None:
    model = _StaticCrossEncoder(RuntimeError("C:/secret/model: sensitive-query"))
    provider = SentenceTransformersCrossEncoderReranker(
        model_id="local/frozen-cross-encoder",
        model=model,
    )
    with pytest.raises(LocalCrossEncoderError) as raised:
        provider.rerank(
            "sensitive-query",
            [RerankCandidate("a", "sensitive-context", 1)],
        )
    assert "sensitive-query" not in str(raised.value)
    assert "sensitive-context" not in str(raised.value)
    assert "C:/secret/model" not in str(raised.value)
