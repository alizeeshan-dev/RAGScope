from __future__ import annotations
import typing

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from backend.app.comparisons.schemas import QueryComparisonCreate
from backend.app.comparisons.service import QueryComparisonService
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Answerability,
    Chunk,
    ClaimSupportStatus,
    ContextSource,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    GeneratedClaim,
    PipelineConfiguration,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RetrievalResultRecord,
    SourceDocument,
    TraceSpan,
    TraceSpanStatus,
)
from backend.app.query_runtime.schemas import QueryRunCreate
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session


def _fixture(session: Session) -> tuple[CorpusVersion, list[PipelineConfiguration], list[Chunk]]:
    corpus = Corpus(name=f"comparison-{uuid4().hex}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="v1",
        status=CorpusVersionStatus.READY,
        embedding_configuration={"provider": "fake", "model": "fake-embed-v1"},
    )
    session.add(version)
    session.flush()
    chunks: list[Chunk] = []
    for index, text in enumerate(("Atlas ocean evidence", "Nimbus rainfall evidence")):
        document = SourceDocument(
            corpus_version_id=version.id,
            title=f"Document {index + 1}",
            file_hash=f"{index + 1:064x}",
            mime_type="text/plain",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            corpus_version_id=version.id,
            chunker_id="comparison-test-v1",
            sequence_number=index,
            text=text,
            token_count=3,
            page_start=index + 1,
            page_end=index + 1,
            section_path=["Results"],
            content_hash=f"{index + 10:064x}",
        )
        session.add(chunk)
        chunks.append(chunk)
    session.flush()
    pipelines = [
        _pipeline(session, "Lexical", RetrievalMode.LEXICAL, temperature=0.0),
        _pipeline(session, "Hybrid", RetrievalMode.HYBRID, temperature=0.5),
        _pipeline(session, "Dense failure", RetrievalMode.DENSE, temperature=0.0),
    ]
    return version, pipelines, chunks


def _pipeline(
    session: Session,
    name: str,
    mode: RetrievalMode,
    *,
    temperature: float,
) -> PipelineConfiguration:
    pipeline = PipelineConfiguration(
        name=f"{name}-{uuid4().hex}",
        version=1,
        retrieval_mode=mode,
        lexical_configuration={"top_k": 2, "candidate_count": 2},
        dense_configuration={"top_k": 2, "candidate_count": 2},
        fusion_configuration={
            "method": "rrf",
            "rrf_k": 60,
            "lexical_weight": 1.0,
            "dense_weight": 1.0,
            "final_count": 2,
        },
        reranker_configuration={
            "enabled": False,
            "model": "fake-reranker-v1",
            "input_candidate_count": 2,
            "final_count": 2,
        },
        query_processing_configuration={"rewriting_enabled": False},
        context_configuration={"token_budget": 100, "deduplicate": True},
        generation_configuration={
            "provider": "fake",
            "model": "fake-generation-v1",
            "temperature": temperature,
            "max_output_tokens": 64,
        },
        citation_configuration={"verification_method": "citation-existence-v1"},
        prompt_versions={"grounded_generation": {"prompt_id": "grounded-answer", "version": 1}},
        configuration_hash=uuid4().hex.ljust(64, "0"),
        frozen_at=datetime.now(UTC),
    )
    session.add(pipeline)
    session.flush()
    return pipeline


def _runner(
    session: Session,
    chunks: list[Chunk],
    *,
    failed_pipeline_id: UUID | None = None,
    wrong_question: bool = False,
) -> typing.Callable[[QueryRunCreate], QueryRun]:
    calls = 0

    def execute(payload: QueryRunCreate) -> QueryRun:
        nonlocal calls
        calls += 1
        failed = payload.pipeline_configuration_id == failed_pipeline_id
        run = QueryRun(
            corpus_version_id=payload.corpus_version_id,
            pipeline_configuration_id=payload.pipeline_configuration_id,
            query_text="different question" if wrong_question else payload.query_text,
            normalized_query=payload.query_text,
            rewritten_query=("atlas evidence" if calls == 2 else None),
            status=QueryRunStatus.FAILED if failed else QueryRunStatus.SUCCEEDED,
            route_decision={"type": "fixed_pipeline"},
            classification={"category": "direct_fact", "confidence": 0.9},
            extracted_metadata={},
            answer_text=None if failed else f"Answer {calls} [S1]",
            answerability_decision=None if failed else Answerability.ANSWERABLE,
            limitations=[],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            total_latency_ms=10 * calls,
            input_tokens=None if failed else 20,
            output_tokens=None if failed else 5,
            estimated_cost=None if failed else calls / 1000,
            failure_code="MODEL_PROVIDER_FAILURE" if failed else None,
            failure_message="Generation failed (fake)." if failed else None,
        )
        session.add(run)
        session.flush()
        session.add(
            TraceSpan(
                query_run_id=run.id,
                sequence_number=1,
                span_type="retrieval",
                name="Retrieval",
                status=TraceSpanStatus.SUCCEEDED,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                latency_ms=4,
                input_summary={},
                output_summary={"candidate_count": calls},
                configuration_snapshot={},
                artifact_ids=[],
            )
        )
        retrieved_chunks = chunks[:calls]
        for rank, chunk in enumerate(retrieved_chunks, 1):
            session.add(
                RetrievalResultRecord(
                    query_run_id=run.id,
                    chunk_id=chunk.id,
                    retriever_type=("hybrid" if calls == 2 else "lexical"),
                    original_rank=(rank if calls == 1 else None),
                    original_score=(1 / rank if calls == 1 else None),
                    fused_rank=(rank if calls == 2 else None),
                    fusion_score=(0.1 / rank if calls == 2 else None),
                    reranked_rank=(2 if rank == 1 and calls == 2 else None),
                    reranker_score=(0.4 if rank == 1 and calls == 2 else None),
                    selected_for_context=rank == 1,
                )
            )
            session.add(
                ContextSource(
                    query_run_id=run.id,
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    citation_id="S1" if rank == 1 else None,
                    sequence_number=rank,
                    selected=rank == 1,
                    exclusion_reason=None if rank == 1 else "token_budget",
                    token_count=chunk.token_count,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            )
        if not failed:
            claim = GeneratedClaim(
                query_run_id=run.id,
                sequence_number=1,
                claim_text=f"Claim {calls}",
                claim_type="factual",
                citation_ids=["S1"],
                support_status=ClaimSupportStatus.NOT_EVALUATED,
                verification_method="citation-existence-v1",
            )
            session.add(claim)
        session.flush()
        return run

    return execute


def test_comparison_api_persists_and_reads_two_inspectable_columns(
    session: Session,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, pipelines, _ = _fixture(session)
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/api/v1/query-comparisons",
        json={
            "corpus_version_id": str(version.id),
            "question": "What evidence describes Atlas?",
            "pipeline_configuration_ids": [
                str(pipelines[0].id),
                str(pipelines[1].id),
            ],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["original_question"] == "What evidence describes Atlas?"
    assert len(payload["columns"]) == 2
    assert all(column["query_run_id"] for column in payload["columns"])
    read = client.get(f"/api/v1/query-comparisons/{payload['id']}")
    assert read.status_code == 200
    assert read.json()["id"] == payload["id"]


def test_comparison_request_requires_two_to_four_distinct_pipelines() -> None:
    with pytest.raises(ValidationError):
        QueryComparisonCreate(
            corpus_version_id=uuid4(),
            question="Question",
            pipeline_configuration_ids=[uuid4()],
        )
    pipeline_id = uuid4()
    with pytest.raises(ValidationError):
        QueryComparisonCreate(
            corpus_version_id=uuid4(),
            question="Question",
            pipeline_configuration_ids=[pipeline_id, pipeline_id],
        )


def test_comparison_aligns_configuration_evidence_and_metrics(session: Session) -> None:
    version, pipelines, chunks = _fixture(session)
    payload = QueryComparisonCreate(
        corpus_version_id=version.id,
        question="What does Atlas measure?",
        pipeline_configuration_ids=[pipelines[0].id, pipelines[1].id],
    )

    result = QueryComparisonService(session).create(payload, runner=_runner(session, chunks))

    assert result.status == "completed"
    assert len(result.columns) == 2
    assert all(column.original_query == payload.question for column in result.columns)
    assert result.columns[1].rewritten_query == "atlas evidence"
    assert result.columns[0].stage_latency_ms == {"1:Retrieval": 4}
    assert result.columns[0].estimated_cost == 0.001
    assert {difference.key for difference in result.configuration_differences} >= {
        "retrieval_mode",
        "generation_temperature",
    }
    assert len(result.evidence_rows) == 2
    second = next(row for row in result.evidence_rows if row.chunk_id == chunks[1].id)
    assert [cell.present for cell in second.cells] == [False, True]
    assert result.evidence_overlap[0].shared_chunk_ids == [chunks[0].id]
    assert result.evidence_overlap[0].right_only_chunk_ids == [chunks[1].id]
    assert result.evidence_overlap[0].jaccard == 0.5


def test_comparison_keeps_failed_pipeline_beside_success(session: Session) -> None:
    version, pipelines, chunks = _fixture(session)
    result = QueryComparisonService(session).create(
        QueryComparisonCreate(
            corpus_version_id=version.id,
            question="Question",
            pipeline_configuration_ids=[pipelines[0].id, pipelines[2].id],
        ),
        runner=_runner(session, chunks, failed_pipeline_id=pipelines[2].id),
    )

    assert result.status == "completed"
    assert "1 of 2" in (result.failure_message or "")
    assert [column.run_status for column in result.columns] == ["succeeded", "failed"]
    assert result.columns[1].failure_code == "MODEL_PROVIDER_FAILURE"
    assert result.columns[1].answer is None


def test_comparison_rejects_runner_that_changes_question(session: Session) -> None:
    version, pipelines, chunks = _fixture(session)
    with pytest.raises(DomainError) as raised:
        QueryComparisonService(session).create(
            QueryComparisonCreate(
                corpus_version_id=version.id,
                question="Original question",
                pipeline_configuration_ids=[pipelines[0].id, pipelines[1].id],
            ),
            runner=_runner(session, chunks, wrong_question=True),
        )
    assert raised.value.code == "COMPARISON_INVARIANT_VIOLATION"


def test_comparison_requires_ready_corpus_and_frozen_pipelines(session: Session) -> None:
    version, pipelines, _ = _fixture(session)
    pipelines[0].frozen_at = None
    with pytest.raises(DomainError) as raised:
        QueryComparisonService(session).create(
            QueryComparisonCreate(
                corpus_version_id=version.id,
                question="Question",
                pipeline_configuration_ids=[pipelines[0].id, pipelines[1].id],
            )
        )
    assert raised.value.code == "ROUTE_UNAVAILABLE"
