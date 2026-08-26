"""Deterministic, offline providers for tests and local development."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .base import (
    GenerationResult,
    RerankCandidate,
    RerankResult,
    StructuredGenerationResult,
)

_TOKEN_RE = re.compile(r"(?u)\b[\w]+\b")


def tokenize(text: str) -> list[str]:
    """Stable lowercase word tokenization used only by deterministic fakes."""

    return _TOKEN_RE.findall(text.casefold())


class DeterministicEmbeddingProvider:
    """Feature-hashing bag-of-words embeddings with L2 normalization.

    This is deliberately not a semantic model. It hashes normalized word tokens
    into a fixed vector and is useful for repeatable dense-index integration tests.
    Exact-term overlap generally raises cosine similarity. It never calls a network.
    """

    provider_id = "fake"
    preprocessing_version = "hashed-bow-v1"

    def __init__(self, dimension: int = 64, model_id: str = "fake-hash-embedding-v1"):
        if dimension < 8:
            raise ValueError("fake embedding dimension must be at least 8")
        self._dimension = dimension
        self._model_id = model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        counts = Counter(tokenize(text))
        for token, count in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            # Signed hashing reduces systematic collision bias. Sublinear TF keeps
            # a repeated term from overwhelming every other query feature.
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class DeterministicGenerationProvider:
    """Stable plain/structured provider for offline tests."""

    provider_id = "fake"

    def __init__(
        self,
        structured_content_override: str | None = None,
        *,
        model_id: str = "fake-generation-v1",
    ) -> None:
        self.structured_content_override = structured_content_override
        self.model_id = model_id

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        if temperature != 0.0:
            raise ValueError("deterministic generation requires temperature=0")
        material = f"{system_prompt or ''}\n{prompt}".encode()
        fingerprint = hashlib.sha256(material).hexdigest()[:16]
        return GenerationResult(
            text=f"Deterministic fake response [{fingerprint}]",
            model_id=self.model_id,
            provider_id=self.provider_id,
            metadata={"fake": True, "input_sha256_prefix": fingerprint},
        )

    def generate_structured(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> StructuredGenerationResult:
        del response_schema
        if temperature != 0.0:
            raise ValueError("deterministic generation requires temperature=0")
        content = self.structured_content_override or self._grounded_content(prompt)
        request_hash = hashlib.sha256(f"{system_prompt}\n{prompt}".encode()).hexdigest()
        request_id = f"fake-{request_hash[:16]}"
        input_tokens = len(tokenize(f"{system_prompt} {prompt}"))
        output_tokens = len(tokenize(content))
        raw = json.dumps(
            {
                "id": request_id,
                "model": self.model_id,
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return StructuredGenerationResult(
            content=content,
            raw_response=raw,
            model_id=self.model_id,
            provider_id=self.provider_id,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=0.0,
            latency_ms=0,
            metadata={
                "fake": True,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
        )

    @staticmethod
    def _grounded_content(prompt: str) -> str:
        match = re.search(
            r"<retrieved_evidence_json>\s*(.*?)\s*</retrieved_evidence_json>",
            prompt,
            flags=re.DOTALL,
        )
        sources: list[dict[str, object]] = []
        if match:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                sources = [item for item in parsed if isinstance(item, dict)]
            elif isinstance(parsed, str):
                sources = [
                    item
                    for item in (
                        json.loads(value)
                        for value in re.findall(
                            r"BEGIN_UNTRUSTED_SOURCE S\d+ JSON_UTF8_BYTES=\d+\n"
                            r"(\{.*?\})\nEND_UNTRUSTED_SOURCE S\d+",
                            parsed,
                            flags=re.DOTALL,
                        )
                    )
                    if isinstance(item, dict)
                ]
        payload: dict[str, object]
        if not sources:
            payload = {
                "answerability": "unanswerable",
                "answer": "The supplied evidence is insufficient to answer the question.",
                "claims": [],
                "limitations": ["No selected evidence was supplied."],
                "abstention_reason": "No selected evidence was supplied.",
            }
        else:
            source = sources[0]
            citation_id = str(source.get("citation_id", "S1"))
            text = " ".join(str(source.get("text", "")).split())
            claim = text[:500].rstrip() or "The selected source contains relevant evidence."
            payload = {
                "answerability": "answerable",
                "answer": f"{claim} [{citation_id}]",
                "claims": [
                    {
                        "text": claim,
                        "citations": [citation_id],
                        "claim_type": "factual",
                    }
                ],
                "limitations": [],
                "abstention_reason": None,
            }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class DeterministicReranker:
    """Offline lexical-overlap stub preserving each candidate's original rank."""

    provider_id = "fake"
    model_id = "fake-token-overlap-reranker-v1"

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        query_terms = set(tokenize(query))
        scored: list[tuple[float, RerankCandidate]] = []
        for candidate in candidates:
            terms = set(tokenize(candidate.text))
            union = query_terms | terms
            score = len(query_terms & terms) / len(union) if union else 0.0
            scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].original_rank, item[1].candidate_id))
        if top_k is not None:
            scored = scored[:top_k]
        return [
            RerankResult(
                candidate_id=candidate.candidate_id,
                original_rank=candidate.original_rank,
                reranked_rank=rank,
                score=score,
            )
            for rank, (score, candidate) in enumerate(scored, start=1)
        ]
