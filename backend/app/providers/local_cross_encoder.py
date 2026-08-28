"""Optional local sentence-transformers CrossEncoder reranker."""

from __future__ import annotations

import importlib
import math
from collections.abc import Iterable, Sequence
from threading import Lock
from typing import Any, Protocol, cast

from .base import RerankCandidate, RerankResult


class LocalCrossEncoderError(RuntimeError):
    """Sanitized local-model failure safe for trace diagnostics."""


class _CrossEncoderModel(Protocol):
    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
    ) -> object: ...


class SentenceTransformersCrossEncoderReranker:
    """Rerank with a local CrossEncoder while preserving retrieval ranks.

    Importing this module and constructing the provider never imports
    ``sentence_transformers`` or downloads a model.  Loading happens on the
    first non-empty rerank call.  ``local_files_only`` defaults to true so the
    production experiment fails clearly rather than changing its environment
    by downloading an unpinned model at execution time.
    """

    provider_id = "sentence_transformers_cross_encoder"

    def __init__(
        self,
        *,
        model_id: str,
        batch_size: int = 16,
        device: str | None = None,
        local_files_only: bool = True,
        revision: str | None = None,
        model: _CrossEncoderModel | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("CrossEncoder model must not be empty")
        if batch_size < 1:
            raise ValueError("CrossEncoder batch size must be positive")
        self._model_id = model_id
        self._batch_size = batch_size
        self._device = device
        self._local_files_only = local_files_only
        self._revision = revision
        self._model = model
        self._model_lock = Lock()

    @property
    def model_id(self) -> str:
        return (
            f"{self._model_id}@{self._revision}"
            if self._revision is not None
            else self._model_id
        )

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        if not candidates:
            return []
        try:
            raw_scores = self._load_model().predict(
                [(query, candidate.text) for candidate in candidates],
                batch_size=self._batch_size,
                show_progress_bar=False,
                convert_to_numpy=False,
            )
            if not isinstance(raw_scores, Iterable) or isinstance(raw_scores, (str, bytes)):
                raise ValueError("CrossEncoder returned non-sequence scores")
            score_values = list(raw_scores)
            if len(score_values) != len(candidates):
                raise ValueError("CrossEncoder score count did not match candidates")
            scores = [float(cast(Any, value)) for value in score_values]
            if not all(math.isfinite(score) for score in scores):
                raise ValueError("CrossEncoder returned a non-finite score")
        except LocalCrossEncoderError:
            raise
        except Exception as exc:
            # Model/runtime messages can include local paths and source text.
            raise LocalCrossEncoderError(
                f"Local CrossEncoder reranking failed ({type(exc).__name__})"
            ) from exc

        ordered = sorted(
            zip(scores, candidates, strict=True),
            key=lambda item: (-item[0], item[1].original_rank, item[1].candidate_id),
        )
        if top_k is not None:
            ordered = ordered[:top_k]
        return [
            RerankResult(
                candidate_id=candidate.candidate_id,
                original_rank=candidate.original_rank,
                reranked_rank=rank,
                score=score,
            )
            for rank, (score, candidate) in enumerate(ordered, start=1)
        ]

    def _load_model(self) -> _CrossEncoderModel:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                module = importlib.import_module("sentence_transformers")
                cross_encoder = module.CrossEncoder
                kwargs: dict[str, object] = {
                    "local_files_only": self._local_files_only,
                }
                if self._device is not None:
                    kwargs["device"] = self._device
                if self._revision is not None:
                    kwargs["revision"] = self._revision
                loaded = cross_encoder(self._model_id, **kwargs)
            except Exception as exc:
                raise LocalCrossEncoderError(
                    "Local CrossEncoder model could not be loaded; install the "
                    "'reranker' extra and cache the frozen model revision"
                ) from exc
            self._model = cast(_CrossEncoderModel, loaded)
            return self._model
