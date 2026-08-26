"""Deterministic Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.app.indexing.schemas import RetrievalResult

from .contracts import RRFConfiguration


@dataclass(frozen=True, slots=True)
class FusedResult:
    chunk_id: str
    rank: int
    score: float
    lexical_contribution: float
    dense_contribution: float


def reciprocal_rank_fusion(
    lexical: Sequence[RetrievalResult],
    dense: Sequence[RetrievalResult],
    *,
    configuration: RRFConfiguration,
    top_k: int | None = None,
) -> list[FusedResult]:
    """Fuse ranks only; raw score scales intentionally do not affect RRF."""

    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be positive")
    contributions: dict[str, list[float]] = {}
    for result in lexical:
        values = contributions.setdefault(result.chunk_id, [0.0, 0.0])
        values[0] = configuration.lexical_weight / (
            configuration.rank_constant + result.rank
        )
    for result in dense:
        values = contributions.setdefault(result.chunk_id, [0.0, 0.0])
        values[1] = configuration.dense_weight / (
            configuration.rank_constant + result.rank
        )
    ordered = sorted(
        contributions.items(),
        key=lambda item: (-(item[1][0] + item[1][1]), item[0]),
    )
    if top_k is not None:
        ordered = ordered[:top_k]
    return [
        FusedResult(
            chunk_id=chunk_id,
            rank=rank,
            score=values[0] + values[1],
            lexical_contribution=values[0],
            dense_contribution=values[1],
        )
        for rank, (chunk_id, values) in enumerate(ordered, start=1)
    ]


def min_max_normalize(results: Sequence[RetrievalResult]) -> dict[str, float]:
    """Normalize for inspection, never for RRF ranking."""

    if not results:
        return {}
    low = min(item.score for item in results)
    high = max(item.score for item in results)
    if high == low:
        return {item.chunk_id: 1.0 for item in results}
    return {item.chunk_id: (item.score - low) / (high - low) for item in results}
