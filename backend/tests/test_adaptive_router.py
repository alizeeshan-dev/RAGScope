from __future__ import annotations

import pytest
from backend.app.adaptive import (
    BestObservedCriteria,
    ClassifierConfiguration,
    CorpusCapabilities,
    DeterministicAdaptiveRouter,
    DeterministicQueryClassifier,
    QueryCategory,
    QueryClassification,
    RouterConfiguration,
    RouteReasonCode,
    RouterInput,
    RunObservation,
    compare_adaptive_to_fixed,
    select_best_observed,
)
from backend.app.core.errors import DomainError
from backend.app.db.models import RetrievalMode
from fastapi.testclient import TestClient


def _classification(category: QueryCategory) -> QueryClassification:
    return QueryClassification(
        category=category,
        reason_code="TEST_FIXTURE",
        reason="Synthetic classification fixture.",
        confidence="high",
        classifier_id="test-classifier",
        classifier_version="test-v1",
        configuration_hash="a" * 64,
    )


def _input(
    query: str,
    category: QueryCategory,
    *,
    capabilities: CorpusCapabilities | None = None,
    metadata_filters: dict[str, str | int | list[str] | list[int]] | None = None,
) -> RouterInput:
    return RouterInput(
        query=query,
        classification=_classification(category),
        metadata_filters=metadata_filters or {},
        capabilities=capabilities
        or CorpusCapabilities(
            lexical_available=True,
            dense_available=True,
            reranker_available=True,
        ),
    )


@pytest.mark.parametrize(
    ("query", "metadata", "expected"),
    [
        ("What is the sample size?", {}, QueryCategory.DIRECT_FACT),
        ("Find a speech dataset", {}, QueryCategory.DATASET_LOOKUP),
        ("Compare COCO versus LAION", {}, QueryCategory.COMPARISON),
        ("Synthesize results across studies", {}, QueryCategory.MULTI_DOCUMENT_SYNTHESIS),
        ("How does collection lead to bias?", {}, QueryCategory.MULTI_HOP_RELATIONSHIP),
        ("Give a broad overview", {}, QueryCategory.BROAD_EXPLORATORY),
        ("Which paper matches this?", {"publication_years": [2020]}, QueryCategory.METADATA_FILTER),
        (
            "Prove that the dataset definitely never failed",
            {},
            QueryCategory.POTENTIALLY_UNANSWERABLE,
        ),
    ],
)
def test_classifier_supports_all_categories_with_versioned_output(
    query: str, metadata: dict[str, object], expected: QueryCategory
) -> None:
    classifier = DeterministicQueryClassifier(
        ClassifierConfiguration(version="classifier-gold-v1")
    )

    result = classifier.classify(query, extracted_metadata=metadata)

    assert result.category is expected
    assert result.reason_code
    assert result.reason
    assert result.classifier_version == "classifier-gold-v1"
    assert len(result.configuration_hash) == 64
    assert classifier.classify(query, extracted_metadata=metadata) == result


def test_router_is_deterministic_and_snapshots_reason_and_versions() -> None:
    router = DeterministicAdaptiveRouter(RouterConfiguration(version="router-gold-v1"))
    value = _input("Compare dataset A versus dataset B", QueryCategory.COMPARISON)

    first = router.route(value)
    second = router.route(value)

    assert first == second
    assert first.retrieval_mode is RetrievalMode.HYBRID
    assert first.reranking_enabled is True
    assert first.rewriting_enabled is True
    assert first.candidate_count == 40
    assert first.context_budget == 8_192
    assert first.reason_code is RouteReasonCode.COMPLEX_EVIDENCE_SEARCH
    assert first.router_version == "router-gold-v1"
    assert first.classifier_version == "test-v1"
    assert len(first.configuration_hash) == 64


@pytest.mark.parametrize(
    ("query", "category", "metadata", "mode", "reason", "expected_candidates"),
    [
        (
            "hello",
            QueryCategory.DIRECT_FACT,
            {},
            RetrievalMode.NONE,
            RouteReasonCode.TRIVIAL_QUERY_NO_RETRIEVAL,
            0,
        ),
        (
            "papers from 2020",
            QueryCategory.METADATA_FILTER,
            {"publication_year": 2020},
            RetrievalMode.LEXICAL,
            RouteReasonCode.METADATA_OR_EXACT_MATCH,
            20,
        ),
        (
            "Find a dataset for speech recognition",
            QueryCategory.DATASET_LOOKUP,
            {},
            RetrievalMode.DENSE,
            RouteReasonCode.SEMANTIC_LOOKUP,
            20,
        ),
        (
            "Explore this scientific topic broadly",
            QueryCategory.BROAD_EXPLORATORY,
            {},
            RetrievalMode.DENSE,
            RouteReasonCode.BROAD_SEMANTIC_SEARCH,
            40,
        ),
        (
            "How does one result depend on another?",
            QueryCategory.MULTI_HOP_RELATIONSHIP,
            {},
            RetrievalMode.HYBRID,
            RouteReasonCode.COMPLEX_EVIDENCE_SEARCH,
            40,
        ),
    ],
)
def test_router_selects_each_route_class_and_reason(
    query: str,
    category: QueryCategory,
    metadata: dict[str, str | int | list[str] | list[int]],
    mode: RetrievalMode,
    reason: RouteReasonCode,
    expected_candidates: int,
) -> None:
    decision = DeterministicAdaptiveRouter().route(
        _input(query, category, metadata_filters=metadata)
    )

    assert decision.retrieval_mode is mode
    assert decision.reason_code is reason
    assert decision.candidate_count == expected_candidates


@pytest.mark.parametrize(
    ("value", "missing_text"),
    [
        (
            _input(
                "Find a speech dataset",
                QueryCategory.DATASET_LOOKUP,
                capabilities=CorpusCapabilities(lexical_available=True, dense_available=False),
            ),
            "retrieval mode 'dense'",
        ),
        (
            _input(
                "Compare A versus B",
                QueryCategory.COMPARISON,
                capabilities=CorpusCapabilities(
                    lexical_available=True, dense_available=True, reranker_available=False
                ),
            ),
            "reranker",
        ),
        (
            _input(
                "Compare A versus B",
                QueryCategory.COMPARISON,
                capabilities=CorpusCapabilities(
                    lexical_available=True,
                    dense_available=True,
                    reranker_available=True,
                    maximum_candidate_count=30,
                    maximum_context_budget=4_096,
                ),
            ),
            "candidate count 40",
        ),
    ],
)
def test_router_rejects_unavailable_routes_without_silent_fallback(
    value: RouterInput, missing_text: str
) -> None:
    with pytest.raises(DomainError) as raised:
        DeterministicAdaptiveRouter().route(value)

    assert raised.value.code == "ROUTE_UNAVAILABLE"
    assert missing_text in raised.value.message


def test_router_rejects_route_excluded_by_frozen_allowed_route_snapshot() -> None:
    router = DeterministicAdaptiveRouter(
        RouterConfiguration(
            allowed_retrieval_modes=(RetrievalMode.NONE, RetrievalMode.LEXICAL)
        )
    )

    with pytest.raises(DomainError) as raised:
        router.route(_input("Find a dataset for vision", QueryCategory.DATASET_LOOKUP))

    assert raised.value.code == "ROUTE_UNAVAILABLE"
    assert "not allowed by router configuration" in raised.value.message


def test_router_uses_configured_counts_and_budgets_without_runtime_mutation() -> None:
    configuration = RouterConfiguration(
        standard_candidate_count=12,
        complex_candidate_count=36,
        standard_context_budget=1_024,
        broad_context_budget=3_000,
        complex_context_budget=6_000,
    )
    router = DeterministicAdaptiveRouter(configuration)

    simple = router.route(_input("Find a dataset for vision", QueryCategory.DATASET_LOOKUP))
    complex_route = router.route(_input("Compare dataset A and B", QueryCategory.COMPARISON))

    assert (simple.candidate_count, simple.context_budget) == (12, 1_024)
    assert (complex_route.candidate_count, complex_route.context_budget) == (36, 6_000)
    assert router.configuration == configuration


def test_best_observed_and_adaptive_deltas_are_explicit_and_missing_safe() -> None:
    criteria = BestObservedCriteria(quality_metric_name="human_correctness", minimum_quality=0.8)
    fixed = RunObservation(
        route_identifier="hybrid-rerank",
        quality_value=0.9,
        retrieval_calls=2,
        reranking_calls=1,
        total_tokens=1_000,
        estimated_cost=0.02,
        latency_ms=800,
    )
    adaptive = RunObservation(
        route_identifier="dense",
        quality_value=0.9,
        retrieval_calls=1,
        reranking_calls=0,
        total_tokens=600,
        estimated_cost=None,
        latency_ms=500,
    )
    failed = RunObservation(
        route_identifier="cheap-failure",
        quality_value=1.0,
        estimated_cost=0.0,
        latency_ms=1,
        infrastructure_failure=True,
    )

    assert select_best_observed([fixed, adaptive, failed], criteria) == fixed
    result = compare_adaptive_to_fixed(
        adaptive=adaptive,
        fixed=fixed,
        observed_candidates=[fixed, adaptive, failed],
        criteria=criteria,
    )

    assert result.best_observed_route_identifier == "hybrid-rerank"
    assert result.best_observed_criteria.quality_metric_name == "human_correctness"
    assert result.route_accuracy_against_best_observed == 0.0
    assert result.answer_quality_delta == 0.0
    assert result.quality_loss == 0.0
    assert result.retrieval_calls_avoided == 1
    assert result.reranking_calls_avoided == 1
    assert result.token_reduction == 400
    assert result.latency_reduction_ms == 300
    assert result.estimated_cost_reduction is None


def test_comparison_leaves_quality_and_best_observed_missing_without_labels() -> None:
    adaptive = RunObservation(route_identifier="none")
    fixed = RunObservation(route_identifier="hybrid", quality_value=None)
    result = compare_adaptive_to_fixed(
        adaptive=adaptive,
        fixed=fixed,
        observed_candidates=[adaptive, fixed],
        criteria=BestObservedCriteria(),
    )

    assert result.best_observed_route_identifier is None
    assert result.route_accuracy_against_best_observed is None
    assert result.answer_quality_delta is None
    assert result.quality_loss is None
    assert result.token_reduction is None


def test_router_configuration_api_versions_and_freezes(client: TestClient) -> None:
    created = client.post(
        "/api/v1/router-configurations",
        json={"name": "pilot-router", "version": 1, "configuration": {}},
    )
    assert created.status_code == 201
    router_id = created.json()["id"]
    assert created.json()["frozen_at"] is None
    assert client.get(f"/api/v1/router-configurations/{router_id}").status_code == 200
    frozen = client.post(f"/api/v1/router-configurations/{router_id}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["frozen_at"] is not None
    repeated = client.post(f"/api/v1/router-configurations/{router_id}/freeze")
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "ROUTER_CONFIGURATION_IMMUTABLE"
