from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest
from backend.app.db.models import (
    Chunk,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    PipelineConfiguration,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RetrievalResultRecord,
    SourceDocument,
)
from backend.app.indexing.errors import IndexNotReady
from backend.app.indexing.schemas import RetrievalResult
from backend.app.indexing.service import IndexingService
from backend.app.providers.fake import DeterministicEmbeddingProvider
from backend.app.retrieval import (
    MetadataFilters,
    RetrievalRequest,
    RetrievalService,
    RetrieverConfiguration,
    RRFConfiguration,
    persist_retrieval_results,
    reciprocal_rank_fusion,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _raw(chunk_id: str, rank: int, score: float, method: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=str(uuid4()),
        corpus_version_id=str(uuid4()),
        text="fixture",
        rank=rank,
        score=score,
        retrieval_method=method,
    )


def test_rrf_matches_hand_calculation_and_breaks_ties_stably() -> None:
    a, b, c = (str(uuid4()) for _ in range(3))
    lexical = [_raw(a, 1, 100.0, "lexical"), _raw(b, 2, 50.0, "lexical")]
    dense = [_raw(b, 1, 0.8, "dense"), _raw(c, 2, 0.7, "dense")]

    fused = reciprocal_rank_fusion(
        lexical,
        dense,
        configuration=RRFConfiguration(rank_constant=10),
    )

    assert fused[0].chunk_id == b
    assert fused[0].score == pytest.approx((1 / 12) + (1 / 11))
    remaining = {row.chunk_id: row for row in fused[1:]}
    assert remaining[a].score == pytest.approx(1 / 11)
    assert remaining[c].score == pytest.approx(1 / 12)


def _add_ready_version(
    session: Session, label: str, documents: Sequence[tuple[str, int, Sequence[str]]]
) -> CorpusVersion:
    corpus = Corpus(name=f"Corpus {label}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label=label,
        embedding_configuration={
            "provider": "fake",
            "model": "fake-hash-embedding-v1",
            "dimension": 32,
            "preprocessing_version": "hashed-bow-v1",
        },
    )
    session.add(version)
    session.flush()
    sequence = 0
    for title, year, texts in documents:
        document = SourceDocument(
            corpus_version_id=version.id,
            title=title,
            publication_year=year,
            file_hash=uuid4().hex + uuid4().hex,
            mime_type="text/plain",
        )
        session.add(document)
        session.flush()
        for text in texts:
            session.add(
                Chunk(
                    document_id=document.id,
                    corpus_version_id=version.id,
                    chunker_id="test-v1",
                    sequence_number=sequence,
                    text=text,
                    token_count=len(text.split()),
                    page_start=sequence + 1,
                    page_end=sequence + 1,
                    content_hash=f"{sequence:064x}",
                )
            )
            sequence += 1
    session.flush()
    IndexingService(
        session, DeterministicEmbeddingProvider(dimension=32)
    ).build_all(version.id)
    return version


def _service(session: Session) -> RetrievalService:
    indexing = IndexingService(session, DeterministicEmbeddingProvider(dimension=32))
    return RetrievalService(session, indexing)


@pytest.mark.parametrize("mode", [RetrievalMode.LEXICAL, RetrievalMode.DENSE])
def test_single_retrievers_honor_top_k_filters_and_corpus_isolation(
    session: Session, mode: RetrievalMode
) -> None:
    version = _add_ready_version(
        session,
        "target",
        [
            ("Old", 2020, ["satellite rainfall old"]),
            ("New", 2024, ["satellite rainfall new", "satellite rainfall current"]),
        ],
    )
    foreign = _add_ready_version(
        session, "foreign", [("Secret", 2024, ["satellite rainfall secret"])]
    )

    execution = _service(session).retrieve(
        RetrievalRequest(
            corpus_version_id=version.id,
            query="satellite rainfall",
            mode=mode,
            top_k=1,
            filters=MetadataFilters(publication_years=frozenset({2024})),
        )
    )

    assert len(execution.candidates) == 1
    assert execution.candidates[0].chunk_id in set(
        session.scalars(select(Chunk.id).where(Chunk.corpus_version_id == version.id))
    )
    assert execution.candidates[0].chunk_id not in set(
        session.scalars(select(Chunk.id).where(Chunk.corpus_version_id == foreign.id))
    )
    assert execution.records[0].original_rank == 1
    assert execution.records[0].normalized_score is not None


def test_hybrid_preserves_raw_ranks_and_adds_distinct_fused_stage(session: Session) -> None:
    version = _add_ready_version(
        session,
        "hybrid",
        [("One", 2024, ["alpha evidence", "alpha beta evidence", "beta only"])],
    )
    execution = _service(session).retrieve(
        RetrievalRequest(
            corpus_version_id=version.id,
            query="alpha beta",
            mode=RetrievalMode.HYBRID,
            top_k=2,
            lexical=RetrieverConfiguration(candidate_count=3),
            dense=RetrieverConfiguration(candidate_count=3),
            fusion=RRFConfiguration(rank_constant=10, lexical_weight=2.0),
        )
    )

    assert len(execution.candidates) == 2
    assert {record.retriever_type for record in execution.records} == {
        "lexical",
        "dense",
        "hybrid",
    }
    assert all(candidate.fused_rank is not None for candidate in execution.candidates)
    assert any(candidate.lexical_rank is not None for candidate in execution.candidates)
    assert any(candidate.dense_rank is not None for candidate in execution.candidates)
    assert all(candidate.rrf_contributions for candidate in execution.candidates)

    configuration = PipelineConfiguration(
        name="hybrid-test",
        version=1,
        retrieval_mode=RetrievalMode.HYBRID,
        configuration_hash="a" * 64,
    )
    session.add(configuration)
    session.flush()
    run = QueryRun(
        corpus_version_id=version.id,
        pipeline_configuration_id=configuration.id,
        query_text="alpha beta",
        normalized_query="alpha beta",
        status=QueryRunStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    first = persist_retrieval_results(session, run.id, execution)
    second = persist_retrieval_results(session, run.id, execution)

    assert [row.id for row in second] == [row.id for row in first]
    persisted = list(
        session.scalars(
            select(RetrievalResultRecord).where(
                RetrievalResultRecord.query_run_id == run.id
            )
        )
    )
    assert len(persisted) == len(execution.records)
    lexical_rows = [row for row in persisted if row.retriever_type == "lexical"]
    hybrid_rows = [row for row in persisted if row.retriever_type == "hybrid"]
    assert all(row.original_rank is not None and row.fused_rank is None for row in lexical_rows)
    assert all(row.original_rank is None and row.fused_rank is not None for row in hybrid_rows)


def test_none_records_disabled_but_still_rejects_unready_corpus(session: Session) -> None:
    version = _add_ready_version(session, "none", [("One", 2024, ["evidence"])])
    execution = _service(session).retrieve(
        RetrievalRequest(
            corpus_version_id=version.id,
            query="question",
            mode=RetrievalMode.NONE,
        )
    )
    assert execution.retrieval_disabled
    assert len(execution.candidates) == 0 and len(execution.records) == 0

    version.status = CorpusVersionStatus.DRAFT
    with pytest.raises(IndexNotReady, match="not ready"):
        _service(session).retrieve(
            RetrievalRequest(
                corpus_version_id=version.id,
                query="question",
                mode=RetrievalMode.NONE,
            )
        )


def test_filter_contract_rejects_invalid_years() -> None:
    with pytest.raises(ValueError, match="four-digit"):
        MetadataFilters(publication_years=frozenset({99}))
