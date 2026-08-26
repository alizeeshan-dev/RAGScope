from __future__ import annotations

from collections.abc import Sequence

from .schemas import (
    AdaptiveComparison,
    BestObservedCriteria,
    RunObservation,
)


def select_best_observed(
    observations: Sequence[RunObservation], criteria: BestObservedCriteria
) -> RunObservation | None:
    eligible = [
        value
        for value in observations
        if value.quality_value is not None
        and (not criteria.exclude_infrastructure_failures or not value.infrastructure_failure)
        and (
            criteria.minimum_quality is None
            or (
                value.quality_value >= criteria.minimum_quality
                if criteria.higher_quality_is_better
                else value.quality_value <= criteria.minimum_quality
            )
        )
    ]
    if not eligible:
        return None

    # Stable sorts apply declared tie-breakers from lowest to highest priority.
    ranked = sorted(eligible, key=lambda value: value.route_identifier)
    for tie_breaker in reversed(criteria.tie_breakers):
        if tie_breaker == "estimated_cost":
            ranked.sort(
                key=lambda value: (
                    value.estimated_cost
                    if value.estimated_cost is not None
                    else float("inf")
                )
            )
        elif tie_breaker == "latency_ms":
            ranked.sort(
                key=lambda value: (
                    float(value.latency_ms) if value.latency_ms is not None else float("inf")
                )
            )
        else:
            ranked.sort(key=lambda value: value.route_identifier)

    def quality_key(value: RunObservation) -> float:
        assert value.quality_value is not None
        return -value.quality_value if criteria.higher_quality_is_better else value.quality_value

    ranked.sort(key=quality_key)
    return ranked[0]


def compare_adaptive_to_fixed(
    *,
    adaptive: RunObservation,
    fixed: RunObservation,
    observed_candidates: Sequence[RunObservation],
    criteria: BestObservedCriteria,
) -> AdaptiveComparison:
    best = select_best_observed(observed_candidates, criteria)
    quality_delta = _difference(adaptive.quality_value, fixed.quality_value)
    if adaptive.quality_value is None or fixed.quality_value is None:
        quality_loss = None
    elif criteria.higher_quality_is_better:
        quality_loss = fixed.quality_value - adaptive.quality_value
    else:
        quality_loss = adaptive.quality_value - fixed.quality_value

    return AdaptiveComparison(
        adaptive_route_identifier=adaptive.route_identifier,
        fixed_route_identifier=fixed.route_identifier,
        best_observed_route_identifier=best.route_identifier if best else None,
        best_observed_criteria=criteria,
        route_accuracy_against_best_observed=_route_accuracy(best, adaptive),
        answer_quality_delta=quality_delta,
        quality_loss=quality_loss,
        retrieval_calls_avoided=_difference(fixed.retrieval_calls, adaptive.retrieval_calls),
        reranking_calls_avoided=_difference(fixed.reranking_calls, adaptive.reranking_calls),
        token_reduction=_difference(fixed.total_tokens, adaptive.total_tokens),
        estimated_cost_reduction=_difference(fixed.estimated_cost, adaptive.estimated_cost),
        latency_reduction_ms=_difference(fixed.latency_ms, adaptive.latency_ms),
    )


def _difference(left: int | float | None, right: int | float | None) -> int | float | None:
    if left is None or right is None:
        return None
    return left - right


def _route_accuracy(best: RunObservation | None, adaptive: RunObservation) -> float | None:
    if best is None:
        return None
    return 1.0 if best.route_identifier == adaptive.route_identifier else 0.0
