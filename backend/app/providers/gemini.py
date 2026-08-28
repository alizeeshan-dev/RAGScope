"""Native Gemini generateContent adapter with structured JSON output."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from backend.app.core.config import Settings
from backend.app.generation.errors import GenerationProviderError, GenerationTimeoutError
from backend.app.generation.schemas import GroundedAnswer

from .base import GenerationResult, StructuredGenerationResult

_GEMINI_SCHEMA_KEYS = {
    "$anchor",
    "$defs",
    "$id",
    "$ref",
    "additionalProperties",
    "anyOf",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minimum",
    "oneOf",
    "prefixItems",
    "properties",
    "propertyOrdering",
    "required",
    "title",
    "type",
}


class GeminiEmbeddingProviderError(RuntimeError):
    """Sanitized Gemini embedding failure safe for job diagnostics."""


class GeminiEmbeddingProvider:
    """Native Gemini ``batchEmbedContents`` adapter for a symmetric index.

    RAGScope's embedding protocol is deliberately symmetric: the same method is
    used for documents and queries.  Consequently this adapter fixes Gemini's
    task type to ``SEMANTIC_SIMILARITY`` instead of silently embedding indexed
    text and queries with incompatible retrieval task types.  Returned vectors
    are L2-normalized before persistence so reduced-dimensional embeddings have
    a stable cosine representation.
    """

    provider_id = "gemini"

    def __init__(
        self,
        *,
        model_id: str,
        dimension: int,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        batch_size: int = 100,
        task_type: str = "SEMANTIC_SIMILARITY",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("RAGSCOPE_GEMINI_API_KEY is required for Gemini embeddings")
        if dimension < 1 or dimension > 4096:
            raise ValueError("Gemini embedding dimension must be between 1 and 4096")
        if timeout_seconds <= 0:
            raise ValueError("Gemini embedding timeout must be positive")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("Gemini embedding batch size must be between 1 and 100")
        if task_type != "SEMANTIC_SIMILARITY":
            raise ValueError(
                "Gemini embeddings require SEMANTIC_SIMILARITY for RAGScope's "
                "shared document/query provider"
            )
        self._model_id = model_id.removeprefix("models/")
        if not self._model_id:
            raise ValueError("Gemini embedding model must not be empty")
        self._dimension = dimension
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size
        self._task_type = task_type
        self._client = client or httpx.Client()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        model_id: str | None = None,
        dimension: int | None = None,
        timeout_seconds: float | None = None,
        batch_size: int | None = None,
        task_type: str | None = None,
        client: httpx.Client | None = None,
    ) -> GeminiEmbeddingProvider:
        if settings.gemini_api_key is None:
            raise ValueError("RAGSCOPE_GEMINI_API_KEY is required for Gemini embeddings")
        return cls(
            model_id=(
                model_id if model_id is not None else settings.gemini_embedding_model
            ),
            dimension=(
                dimension if dimension is not None else settings.gemini_embedding_dimension
            ),
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key.get_secret_value(),
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.embedding_timeout_seconds
            ),
            batch_size=(
                batch_size if batch_size is not None else settings.gemini_embedding_batch_size
            ),
            task_type=(
                task_type if task_type is not None else settings.gemini_embedding_task_type
            ),
            client=client,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def preprocessing_version(self) -> str:
        return f"gemini-{self._task_type.casefold().replace('_', '-')}-l2-v1"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed_batch(texts[offset : offset + self._batch_size]))
        if len(vectors) != len(texts):
            raise GeminiEmbeddingProviderError(
                "Gemini embedding response count did not match the request"
            )
        return vectors

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        model_name = f"models/{self.model_id}"
        payload = {
            "requests": [
                {
                    "model": model_name,
                    "content": {"parts": [{"text": text}]},
                    "taskType": self._task_type,
                    "outputDimensionality": self.dimension,
                }
                for text in texts
            ]
        }
        try:
            response = self._client.post(
                f"{self._base_url}/v1beta/models/"
                f"{quote(self.model_id, safe='')}:batchEmbedContents",
                headers={
                    "x-goog-api-key": self._api_key,
                    "x-goog-api-client": "ragscope/0.1.0",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            parsed = response.json()
            embeddings = parsed["embeddings"]
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise ValueError("unexpected embedding count")
            return [self._validated_vector(item["values"]) for item in embeddings]
        except (
            httpx.HTTPError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            # Never copy provider bodies, URLs, request headers, or source text
            # into an error that may be persisted with an indexing job.
            raise GeminiEmbeddingProviderError(
                f"Gemini embedding request failed ({type(exc).__name__})"
            ) from exc

    def _validated_vector(self, values: object) -> list[float]:
        if not isinstance(values, list) or len(values) != self.dimension:
            raise ValueError("unexpected embedding dimension")
        vector = [float(value) for value in values]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding contains a non-finite value")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("embedding has zero magnitude")
        return [value / norm for value in vector]


def _gemini_json_schema(value: object) -> object:
    """Keep only the JSON Schema subset accepted by Gemini structured output."""

    if isinstance(value, list):
        return [_gemini_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, object] = {}
    for key, item in value.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            continue
        if key in {"properties", "$defs"} and isinstance(item, dict):
            cleaned[key] = {str(name): _gemini_json_schema(schema) for name, schema in item.items()}
        else:
            cleaned[key] = _gemini_json_schema(item)
    return cleaned


class GeminiGenerationProvider:
    """Opt-in Google Gemini REST adapter.

    The credential is used only in ``x-goog-api-key`` and is never returned in
    metadata, errors, persisted configuration, or raw request data.
    """

    provider_id = "gemini"

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("RAGSCOPE_GEMINI_API_KEY is required for Gemini generation")
        self._model_id = model_id.removeprefix("models/")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._input_price = input_price_per_million
        self._output_price = output_price_per_million
        self._client = client or httpx.Client()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        model_id: str | None = None,
        timeout_seconds: float | None = None,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        client: httpx.Client | None = None,
    ) -> GeminiGenerationProvider:
        if settings.gemini_api_key is None:
            raise ValueError("RAGSCOPE_GEMINI_API_KEY is required for Gemini generation")
        return cls(
            model_id=model_id or settings.gemini_model,
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key.get_secret_value(),
            timeout_seconds=timeout_seconds or settings.generation_timeout_seconds,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
            client=client,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        result = self._request(
            prompt,
            system_prompt=system_prompt or "",
            temperature=temperature,
            max_output_tokens=None,
            structured=False,
        )
        return GenerationResult(
            text=result.content,
            model_id=result.model_id,
            provider_id=result.provider_id,
            metadata={
                **dict(result.metadata),
                "provider_request_id": result.request_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "estimated_cost": result.estimated_cost,
                "latency_ms": result.latency_ms,
            },
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
        return self._request(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            structured=True,
            response_schema=response_schema,
        )

    def _request(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float,
        max_output_tokens: int | None,
        structured: bool,
        response_schema: Mapping[str, Any] | None = None,
    ) -> StructuredGenerationResult:
        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        if structured:
            generation_config.update(
                {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": _gemini_json_schema(
                        response_schema or GroundedAnswer.model_json_schema()
                    ),
                }
            )
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": generation_config,
            "store": False,
        }
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self._base_url}/v1beta/models/{quote(self.model_id, safe='')}:generateContent",
                headers={
                    "x-goog-api-key": self._api_key,
                    "x-goog-api-client": "ragscope/0.1.0",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            parsed = response.json()
            candidates = parsed["candidates"]
            parts = candidates[0]["content"]["parts"]
            content = "".join(
                part["text"]
                for part in parts
                if isinstance(part.get("text"), str) and not part.get("thought", False)
            )
            if not content:
                raise ValueError("Gemini returned no text content")
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError("Gemini generation request timed out") from exc
        except (
            httpx.HTTPError,
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            # Provider bodies can contain user/model-controlled data. Do not copy
            # them into the sanitized application error.
            raise GenerationProviderError(
                f"Gemini generation request failed ({type(exc).__name__})"
            ) from exc

        latency_ms = round((time.perf_counter() - started) * 1_000)
        usage = parsed.get("usageMetadata", {})
        input_tokens = self._optional_int(usage.get("promptTokenCount"))
        output_tokens = self._optional_int(usage.get("candidatesTokenCount"))
        thoughts_tokens = self._optional_int(usage.get("thoughtsTokenCount"))
        candidate = candidates[0]
        request_id = (
            parsed.get("responseId")
            or response.headers.get("x-goog-request-id")
            or response.headers.get("x-request-id")
        )
        return StructuredGenerationResult(
            content=content,
            raw_response=response.text,
            model_id=str(parsed.get("modelVersion") or self.model_id),
            provider_id=self.provider_id,
            request_id=str(request_id) if request_id is not None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=self._cost(
                input_tokens,
                (output_tokens + (thoughts_tokens or 0) if output_tokens is not None else None),
            ),
            latency_ms=latency_ms,
            metadata={
                "finish_reason": candidate.get("finishReason"),
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "response_format": "application/json" if structured else "text/plain",
                "cached_content_tokens": self._optional_int(usage.get("cachedContentTokenCount")),
                "thoughts_tokens": thoughts_tokens,
                "total_tokens": self._optional_int(usage.get("totalTokenCount")),
            },
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        if (
            input_tokens is None
            or output_tokens is None
            or self._input_price is None
            or self._output_price is None
        ):
            return None
        return (input_tokens * self._input_price + output_tokens * self._output_price) / 1_000_000
