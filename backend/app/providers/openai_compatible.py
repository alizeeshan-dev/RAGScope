"""Opt-in OpenAI-compatible Chat Completions adapter.

Credentials are accepted only from the environment-backed Settings object. They
are used in the Authorization header and never included in returned metadata.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from backend.app.core.config import Settings
from backend.app.generation.errors import GenerationProviderError, GenerationTimeoutError

from .base import GenerationResult, StructuredGenerationResult


class OpenAICompatibleGenerationProvider:
    provider_id = "openai_compatible"

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
            raise ValueError("RAGSCOPE_GENERATION_API_KEY is required for real generation")
        self._model_id = model_id
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
    ) -> OpenAICompatibleGenerationProvider:
        if settings.generation_api_key is None:
            raise ValueError("RAGSCOPE_GENERATION_API_KEY is required for real generation")
        return cls(
            model_id=model_id or settings.generation_model,
            base_url=settings.generation_base_url,
            api_key=settings.generation_api_key.get_secret_value(),
            timeout_seconds=timeout_seconds or settings.generation_timeout_seconds,
            input_price_per_million=(
                input_price_per_million
                if input_price_per_million is not None
                else settings.generation_input_price_per_million
            ),
            output_price_per_million=(
                output_price_per_million
                if output_price_per_million is not None
                else settings.generation_output_price_per_million
            ),
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
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
        }
        if structured:
            payload["response_format"] = (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ragscope_structured_output",
                        "strict": True,
                        "schema": response_schema,
                    },
                }
                if response_schema is not None
                else {"type": "json_object"}
            )
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            parsed = response.json()
            content = parsed["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("provider message content is not text")
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError("OpenAI-compatible generation request timed out") from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Do not include response bodies or request headers: either may contain
            # provider-controlled sensitive data.
            raise GenerationProviderError(
                f"OpenAI-compatible generation request failed ({type(exc).__name__})"
            ) from exc
        latency_ms = round((time.perf_counter() - started) * 1_000)
        usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
        input_tokens = self._optional_int(usage.get("prompt_tokens"))
        output_tokens = self._optional_int(usage.get("completion_tokens"))
        estimated_cost = self._cost(input_tokens, output_tokens)
        choice = parsed["choices"][0]
        request_id = parsed.get("id") or response.headers.get("x-request-id")
        return StructuredGenerationResult(
            content=content,
            raw_response=response.text,
            model_id=str(parsed.get("model") or self.model_id),
            provider_id=self.provider_id,
            request_id=str(request_id) if request_id is not None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            latency_ms=latency_ms,
            metadata={
                "finish_reason": choice.get("finish_reason"),
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "response_format": "json_object" if structured else "text",
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
