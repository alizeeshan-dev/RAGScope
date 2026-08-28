from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from backend.app.adaptive.schemas import (
    RouterConfiguration,
    RouterConfigurationCreate,
)
from backend.app.adaptive.service import (
    create_router_configuration,
    freeze_router_configuration,
)
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Artifact,
    Benchmark,
    BenchmarkAnnotationStatus,
    BenchmarkDifficulty,
    BenchmarkEvidenceReference,
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    BenchmarkQuestionType,
    BenchmarkVersion,
    BenchmarkVersionStatus,
    Chunk,
    CitationVerification,
    ClaimSupportStatus,
    ContextSource,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    GeneratedClaim,
    PipelineConfiguration,
    PipelineExecutionMode,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RetrievalResultRecord,
    SourceDocument,
    TraceSpan,
    TraceSpanStatus,
)
from backend.app.evaluation.schemas import HumanMetricCreate
from backend.app.evaluation.service import EvaluationService
from backend.app.indexing.service import IndexingService
from backend.app.pipelines.schemas import (
    AdaptivePipelineConfiguration,
    PipelineConfigurationCreate,
)
from backend.app.pipelines.service import (
    create_pipeline_configuration,
    freeze_pipeline_configuration,
)
from backend.app.providers.fake import DeterministicEmbeddingProvider
from backend.app.query_runtime.schemas import QueryRunCreate
from backend.app.query_runtime.service import QueryOrchestrator
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _ready_corpus(session: Session) -> CorpusVersion:
    corpus = Corpus(name=f"Runtime {uuid4().hex}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="v1",
        embedding_configuration={
            "provider": "fake",
            "model": "fake-hash-embedding-v1",
            "dimension": 32,
            "preprocessing_version": "hashed-bow-v1",
        },
    )
    session.add(version)
    session.flush()
    for number, (title, year, text) in enumerate(
        [
            ("Atlas", 2024, "Atlas measures daily ocean surface temperature."),
            ("Rain", 2023, "Nimbus measures hourly tropical rainfall."),
            ("Atlas methods", 2024, "Ocean temperature uses calibrated satellites."),
        ]
    ):
        document = SourceDocument(
            corpus_version_id=version.id,
            title=title,
            publication_year=year,
            file_hash=f"{number + 1:064x}",
            mime_type="text/plain",
        )
        session.add(document)
        session.flush()
        session.add(
            Chunk(
                document_id=document.id,
                corpus_version_id=version.id,
                chunker_id="runtime-test-v1",
                sequence_number=number,
                text=text,
                token_count=len(text.split()),
                page_start=number + 1,
                page_end=number + 1,
                content_hash=f"{number + 11:064x}",
            )
        )
    session.flush()
    IndexingService(session, DeterministicEmbeddingProvider(dimension=32)).build_all(version.id)
    return version


def _pipeline(
    session: Session, mode: RetrievalMode, *, rerank: bool = False
) -> PipelineConfiguration:
    payload = PipelineConfigurationCreate(
        name=f"{mode.value}-{rerank}-{uuid4().hex}",
        retrieval_mode=mode,
        lexical_configuration={"top_k": 2, "candidate_count": 3},
        dense_configuration={
            "top_k": 2,
            "candidate_count": 3,
            "similarity_method": "cosine",
        },
        fusion_configuration={
            "method": "rrf",
            "rrf_k": 10,
            "lexical_weight": 1,
            "dense_weight": 1,
            "final_count": 3 if rerank else 2,
        },
        reranker_configuration={
            "enabled": rerank,
            "provider": "fake",
            "model": "fake-token-overlap-reranker-v1",
            "input_candidate_count": 3,
            "final_count": 2,
        },
        context_configuration={
            "token_budget": 300,
            "deduplicate": True,
            "overlap_threshold": 0.9,
        },
    )
    pipeline = create_pipeline_configuration(session, payload)
    freeze_pipeline_configuration(session, pipeline)
    return pipeline


@pytest.mark.parametrize(
    ("mode", "rerank"),
    [
        (RetrievalMode.NONE, False),
        (RetrievalMode.LEXICAL, False),
        (RetrievalMode.DENSE, False),
        (RetrievalMode.HYBRID, False),
        (RetrievalMode.HYBRID, True),
    ],
)
def test_complete_fixed_pipeline_modes(
    session: Session, tmp_path: Path, mode: RetrievalMode, rerank: bool
) -> None:
    version = _ready_corpus(session)
    pipeline = _pipeline(session, mode, rerank=rerank)
    store = LocalArtifactStore(tmp_path / "artifacts")

    run = QueryOrchestrator(
        session,
        settings=Settings(artifact_root=tmp_path / "artifacts"),
        artifact_store=store,
    ).execute(
        QueryRunCreate(
            corpus_version_id=version.id,
            pipeline_configuration_id=pipeline.id,
            query_text="What does Atlas measure?",
        )
    )

    assert run.status == QueryRunStatus.SUCCEEDED
    assert run.prompt_template_id is not None
    assert run.context_artifact_id is not None
    assert run.raw_response_artifact_id is not None
    assert run.total_latency_ms is not None
    assert run.input_tokens is not None and run.output_tokens is not None
    context_artifact = session.get(Artifact, run.context_artifact_id)
    assert context_artifact is not None
    context_bytes = store.read_bytes(context_artifact.storage_key)
    if mode == RetrievalMode.NONE:
        assert context_bytes == b""
        assert run.answerability_decision is not None
        assert run.route_decision["retrieval_disabled"] is True
        assert not list(
            session.scalars(
                select(RetrievalResultRecord).where(RetrievalResultRecord.query_run_id == run.id)
            )
        )
    else:
        assert b"BEGIN_UNTRUSTED_SOURCE S1" in context_bytes
        assert (
            session.scalar(select(GeneratedClaim).where(GeneratedClaim.query_run_id == run.id))
            is not None
        )
    if mode == RetrievalMode.HYBRID:
        records = list(
            session.scalars(
                select(RetrievalResultRecord).where(RetrievalResultRecord.query_run_id == run.id)
            )
        )
        assert {record.retriever_type for record in records} == {
            "lexical",
            "dense",
            "hybrid",
        }
        assert all(
            record.original_rank is not None and record.fused_rank is None
            for record in records
            if record.retriever_type in {"lexical", "dense"}
        )
        if rerank:
            assert any(record.reranked_rank is not None for record in records)

    spans = list(
        session.scalars(
            select(TraceSpan)
            .where(TraceSpan.query_run_id == run.id)
            .order_by(TraceSpan.sequence_number)
        )
    )
    span_types = [span.span_type for span in spans]
    assert span_types[:4] == [
        "query_processing",
        "classification",
        "rewriting",
        "routing",
    ]
    expected_retrieval_children = {
        RetrievalMode.NONE: [],
        RetrievalMode.LEXICAL: ["lexical"],
        RetrievalMode.DENSE: ["dense"],
        RetrievalMode.HYBRID: ["lexical", "dense", "fusion"],
    }[mode]
    assert span_types[4 : 5 + len(expected_retrieval_children)] == [
        "retrieval",
        *expected_retrieval_children,
    ]
    assert span_types[-5:] == [
        "reranking",
        "context_construction",
        "generation",
        "claim_processing",
        "citation_validation",
    ]
    assert [span.sequence_number for span in spans] == list(range(1, len(spans) + 1))
    assert all(span.status == TraceSpanStatus.SUCCEEDED for span in spans)
    by_type = {span.span_type: span for span in spans}
    assert by_type["classification"].parent_span_id == by_type["query_processing"].id
    assert by_type["rewriting"].parent_span_id == by_type["query_processing"].id
    for child_type in expected_retrieval_children:
        assert by_type[child_type].parent_span_id == by_type["retrieval"].id
    assert by_type["citation_validation"].parent_span_id == by_type["claim_processing"].id
    assert str(run.context_artifact_id) in by_type["context_construction"].artifact_ids
    assert str(run.raw_response_artifact_id) in by_type["generation"].artifact_ids


def test_orchestrator_rejects_unready_corpus(session: Session, tmp_path: Path) -> None:
    corpus = Corpus(name="Unready")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="draft",
        status=CorpusVersionStatus.DRAFT,
    )
    session.add(version)
    pipeline = _pipeline(session, RetrievalMode.NONE)
    with pytest.raises(DomainError) as raised:
        QueryOrchestrator(
            session,
            settings=Settings(artifact_root=tmp_path / "artifacts"),
        ).execute(
            QueryRunCreate(
                corpus_version_id=version.id,
                pipeline_configuration_id=pipeline.id,
                query_text="Question",
            )
        )
    assert raised.value.code == "CORPUS_VERSION_NOT_READY"


def test_retrieval_failure_finalizes_durable_run(session: Session, tmp_path: Path) -> None:
    corpus = Corpus(name="Missing index")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="ready-without-index",
        status=CorpusVersionStatus.READY,
        embedding_configuration={
            "provider": "fake",
            "model": "fake-hash-embedding-v1",
            "dimension": 32,
            "preprocessing_version": "hashed-bow-v1",
        },
    )
    session.add(version)
    pipeline = _pipeline(session, RetrievalMode.LEXICAL)
    with pytest.raises(DomainError) as raised:
        QueryOrchestrator(
            session,
            settings=Settings(artifact_root=tmp_path / "artifacts"),
        ).execute(
            QueryRunCreate(
                corpus_version_id=version.id,
                pipeline_configuration_id=pipeline.id,
                query_text="Question",
            )
        )
    assert raised.value.code == "RETRIEVAL_FAILED"
    persisted = session.scalar(select(QueryRun).where(QueryRun.corpus_version_id == version.id))
    assert persisted is not None
    assert persisted.status == QueryRunStatus.FAILED
    assert persisted.failure_code == "RETRIEVAL_FAILED"
    spans = list(
        session.scalars(
            select(TraceSpan)
            .where(TraceSpan.query_run_id == persisted.id)
            .order_by(TraceSpan.sequence_number)
        )
    )
    assert [span.span_type for span in spans] == [
        "query_processing",
        "classification",
        "rewriting",
        "routing",
        "retrieval",
        "lexical",
    ]
    assert all(span.status == TraceSpanStatus.SUCCEEDED for span in spans[:4])
    assert all(span.status == TraceSpanStatus.FAILED for span in spans[4:])
    assert all(span.error_code == "RETRIEVAL_FAILED" for span in spans[4:])


def test_query_run_read_endpoints(session: Session, client: TestClient, tmp_path: Path) -> None:
    version = _ready_corpus(session)
    pipeline = _pipeline(session, RetrievalMode.LEXICAL)
    run = QueryOrchestrator(
        session,
        settings=Settings(artifact_root=tmp_path / "artifacts"),
    ).execute(
        QueryRunCreate(
            corpus_version_id=version.id,
            pipeline_configuration_id=pipeline.id,
            query_text="Atlas ocean temperature",
            filters={"publication_years": [2024]},
        )
    )
    for suffix in ("", "/retrieval-results", "/claims", "/context", "/trace"):
        response = client.get(f"/api/v1/query-runs/{run.id}{suffix}")
        assert response.status_code == 200
        if suffix == "/trace":
            payload = response.json()
            assert payload["schema_version"] == "ragscope.observable-trace.v1"
            assert payload["run"]["id"] == str(run.id)
            assert payload["summary"]["retrieval_candidate_count"] > 0


def test_query_run_post_executes_orchestrator(
    session: Session,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _ready_corpus(session)
    pipeline = _pipeline(session, RetrievalMode.NONE)
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/api/v1/query-runs",
        json={
            "corpus_version_id": str(version.id),
            "pipeline_configuration_id": str(pipeline.id),
            "query_text": "What does Atlas measure?",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
    assert response.json()["route_decision"]["retrieval_disabled"] is True
    invalid = client.post(
        "/api/v1/query-runs",
        json={
            "corpus_version_id": str(version.id),
            "pipeline_configuration_id": str(pipeline.id),
            "query_text": " \t\n ",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_QUERY"


def test_adaptive_pipeline_reuses_runtime_and_persists_route_reason(
    session: Session, tmp_path: Path
) -> None:
    version = _ready_corpus(session)
    router = create_router_configuration(
        session,
        RouterConfigurationCreate(
            name=f"adaptive-{uuid4().hex}",
            configuration=RouterConfiguration(
                standard_candidate_count=3,
                complex_candidate_count=4,
                standard_context_budget=300,
                broad_context_budget=400,
                complex_context_budget=500,
            ),
        ),
    )
    freeze_router_configuration(session, router)
    pipeline = create_pipeline_configuration(
        session,
        PipelineConfigurationCreate(
            name=f"adaptive-pipeline-{uuid4().hex}",
            execution_mode=PipelineExecutionMode.ADAPTIVE,
            router_configuration_id=router.id,
            adaptive_configuration=AdaptivePipelineConfiguration(
                maximum_candidate_count=10,
                maximum_context_budget=1_000,
            ),
            retrieval_mode=RetrievalMode.HYBRID,
            dense_configuration={
                "top_k": 3,
                "candidate_count": 3,
                "similarity_method": "cosine",
            },
            context_configuration={"token_budget": 300},
        ),
    )
    freeze_pipeline_configuration(session, pipeline)

    run = QueryOrchestrator(
        session,
        settings=Settings(artifact_root=tmp_path / "artifacts"),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
    ).execute(
        QueryRunCreate(
            corpus_version_id=version.id,
            pipeline_configuration_id=pipeline.id,
            query_text="Find a speech dataset",
        )
    )

    assert run.status is QueryRunStatus.SUCCEEDED
    assert run.route_decision["type"] == "adaptive"
    assert run.route_decision["retrieval_mode"] == "dense"
    assert run.route_decision["reason_code"] == "SEMANTIC_LOOKUP"
    assert run.route_decision["router_version"] == "1.0.0"
    spans = list(
        session.scalars(
            select(TraceSpan)
            .where(TraceSpan.query_run_id == run.id)
            .order_by(TraceSpan.sequence_number)
        )
    )
    assert [span.span_type for span in spans].index("routing") < [
        span.span_type for span in spans
    ].index("retrieval")


def test_benchmark_linked_run_evaluates_and_preserves_human_labels(
    session: Session, client: TestClient, tmp_path: Path
) -> None:
    version = _ready_corpus(session)
    pipeline = _pipeline(session, RetrievalMode.LEXICAL)
    run = QueryOrchestrator(
        session,
        settings=Settings(artifact_root=tmp_path / "artifacts"),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
    ).execute(
        QueryRunCreate(
            corpus_version_id=version.id,
            pipeline_configuration_id=pipeline.id,
            query_text="What does Atlas measure?",
        )
    )
    selected = session.scalar(
        select(ContextSource)
        .where(ContextSource.query_run_id == run.id, ContextSource.selected.is_(True))
        .order_by(ContextSource.sequence_number)
    )
    assert selected is not None
    chunk = session.get(Chunk, selected.chunk_id)
    assert chunk is not None
    benchmark = Benchmark(name=f"evaluation-{uuid4().hex}")
    session.add(benchmark)
    session.flush()
    benchmark_version = BenchmarkVersion(
        benchmark_id=benchmark.id,
        corpus_version_id=version.id,
        version=1,
        status=BenchmarkVersionStatus.DRAFT,
    )
    session.add(benchmark_version)
    session.flush()
    question = BenchmarkQuestion(
        benchmark_version_id=benchmark_version.id,
        question_text=run.query_text,
        question_type=BenchmarkQuestionType.DIRECT_FACT_LOOKUP,
        difficulty=BenchmarkDifficulty.EASY,
        answerable=True,
        expected_answerability="answerable",
        reference_answer="Atlas measures rainfall and temperature.",
        answer_criteria="Identify the measurements.",
        required_document_ids=[str(chunk.document_id)],
        required_chunk_ids=[str(chunk.id)],
        tags=["evaluation"],
        annotation_status=BenchmarkAnnotationStatus.REVIEWED,
    )
    session.add(question)
    session.flush()
    evidence_set = BenchmarkEvidenceSet(
        benchmark_question_id=question.id,
        set_number=1,
        description="Primary supporting passage",
    )
    session.add(evidence_set)
    session.flush()
    session.add(
        BenchmarkEvidenceReference(
            evidence_set_id=evidence_set.id,
            document_id=chunk.document_id,
            page_number=chunk.page_start,
            chunk_id=chunk.id,
            selected_text=chunk.text,
            evidence_role="required",
        )
    )
    run.benchmark_question_id = question.id
    session.flush()
    benchmark_version.status = BenchmarkVersionStatus.FROZEN
    benchmark_version.frozen_at = datetime.now(UTC)
    session.flush()

    service = EvaluationService(session)
    metrics, _ = service.evaluate(run.id)
    values = {metric.metric_name: metric.metric_value for metric in metrics}
    assert values["recall_at_k"] == 1.0
    assert values["required_evidence_retained"] == 1.0
    assert values["answer_correctness"] is None
    service.add_human_metric(
        run.id,
        HumanMetricCreate(
            metric_name="answer_correctness",
            metric_value=1.0,
            reviewer_label="reviewed-correct",
        ),
    )
    citation_review = service.add_human_metric(
        run.id,
        HumanMetricCreate(
            metric_name="citation_precision",
            metric_value=1.0,
            reviewer_label="reviewed-citations",
        ),
    )
    assert citation_review.metric_scope.value == "citation"
    service.evaluate(run.id)

    correctness = [
        metric
        for metric in service.list_results(run.id)
        if metric.metric_name == "answer_correctness"
    ]
    assert any(
        metric.evaluation_method == "human" and metric.metric_value == 1.0
        for metric in correctness
    )
    assert any(metric.metric_value is None for metric in correctness)
    assert session.scalar(select(CitationVerification)) is not None
    assert all(
        claim.support_status is not ClaimSupportStatus.NOT_EVALUATED
        for claim in run.claims
    )
    response = client.get(f"/api/v1/query-runs/{run.id}/evaluation")
    assert response.status_code == 200
    assert response.json()["benchmark_question_id"] == str(question.id)
    assert any(
        metric["metric_name"] == "recall_at_k" for metric in response.json()["metrics"]
    )
