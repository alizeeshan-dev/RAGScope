from __future__ import annotations

import pytest
from backend.app.indexing.schemas import SearchFilters
from backend.app.indexing.scoring import (
    SearchRecord,
    cosine_similarity,
    portable_dense_search,
    portable_lexical_search,
)
from backend.app.providers.fake import DeterministicEmbeddingProvider


def record(
    identifier: str, text: str, *, version: str = "v1", document: str = "d1"
) -> SearchRecord:
    return SearchRecord(
        chunk_id=identifier,
        document_id=document,
        corpus_version_id=version,
        text=text,
        metadata={"kind": "paper"},
    )


def test_portable_lexical_ranking_is_tfidf_cosine_not_bm25() -> None:
    results = portable_lexical_search(
        "ocean temperature",
        [
            record("exact", "ocean temperature"),
            record("partial", "ocean salinity measurements"),
            record("miss", "forest canopy height"),
        ],
        top_k=3,
    )
    assert [result.chunk_id for result in results] == ["exact", "partial"]
    assert results[0].retrieval_method == "lexical_tfidf_cosine"
    assert results[0].score > results[1].score > 0


def test_lexical_filters_are_applied_before_corpus_statistics() -> None:
    results = portable_lexical_search(
        "dataset",
        [
            record("one", "dataset", document="allowed"),
            record("two", "dataset", document="excluded"),
        ],
        top_k=2,
        filters=SearchFilters(document_ids=frozenset({"allowed"})),
    )
    assert [result.document_id for result in results] == ["allowed"]


def test_fake_dense_search_is_deterministic() -> None:
    provider = DeterministicEmbeddingProvider(dimension=64)
    source = [
        record("best", "satellite precipitation dataset"),
        record("other", "protein folding microscopy"),
    ]
    vectors = provider.embed([item.text for item in source])
    embedded = [
        SearchRecord(
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            corpus_version_id=item.corpus_version_id,
            text=item.text,
            metadata=item.metadata,
            embedding=vector,
        )
        for item, vector in zip(source, vectors, strict=True)
    ]
    query = provider.embed(["precipitation satellite"])[0]
    first = portable_dense_search(query, embedded, top_k=2)
    second = portable_dense_search(query, embedded, top_k=2)
    assert first == second
    assert first[0].chunk_id == "best"
    assert first[0].score > first[1].score


def test_cosine_rejects_mixed_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        cosine_similarity([1.0], [1.0, 2.0])
