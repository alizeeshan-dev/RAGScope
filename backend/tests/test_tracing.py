from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.db.models import (
    Artifact,
    Chunk,
    ClaimSupportStatus,
    ContextSource,
    Corpus,
    CorpusVersion,
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
from backend.app.tracing import (
    REDACTED,
    SpanRecorder,
    TraceRecordingError,
    build_trace_summary,
    export_observable_trace,
    redact,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _run(session: Session) -> QueryRun:
    corpus = Corpus(name=f"trace-{uuid4()}")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(corpus_id=corpus.id, version_label="v1")
    pipeline = PipelineConfiguration(
        name=f"trace-pipeline-{uuid4()}",
        version=1,
        retrieval_mode=RetrievalMode.HYBRID,
        lexical_configuration={"top_k": 10},
        dense_configuration={"candidate_count": 20},
        fusion_configuration={"method": "rrf", "rrf_k": 60},
        reranker_configuration={"enabled": True},
        query_processing_configuration={"rewriting_enabled": False},
        context_configuration={"token_budget": 500},
        generation_configuration={"provider": "fake"},
        citation_configuration={},
        prompt_versions={"grounded_generation": 1},
        configuration_hash="a" * 64,
        frozen_at=datetime.now(UTC),
    )
    session.add_all([version, pipeline])
    session.flush()
    run = QueryRun(
        corpus_version_id=version.id,
        pipeline_configuration_id=pipeline.id,
        query_text="What is measured?",
        normalized_query="What is measured?",
        status=QueryRunStatus.RUNNING,
        route_decision={"retrieval_mode": "hybrid"},
        classification={"category": "direct_fact"},
        extracted_metadata={},
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def test_recursive_redaction_handles_keys_values_and_json_normalization() -> None:
    secret = "credential-value-123"
    value = {
        "headers": {"Authorization": f"Bearer {secret}", "Accept": "json"},
        "geminiApiKey": secret,
        "nested": [f"prefix:{secret}", uuid4()],
        "database_url": "postgresql://user:password@localhost/db",
        "endpoint": "https://example.test/run?api_key=another-secret&mode=json",
        "provider_credentials": {"value": "hidden"},
    }

    safe = redact(value, sensitive_values=(secret,))

    assert safe["headers"]["Authorization"] == REDACTED
    assert safe["geminiApiKey"] == REDACTED
    assert safe["nested"][0] == f"prefix:{REDACTED}"
    assert isinstance(safe["nested"][1], str)
    assert safe["database_url"] == REDACTED
    assert safe["endpoint"] == (
        f"https://example.test/run?api_key={REDACTED}&mode=json"
    )
    assert safe["provider_credentials"] == REDACTED
    assert secret not in json.dumps(safe)


def test_span_recorder_preserves_order_hierarchy_status_and_redaction(
    session: Session,
) -> None:
    run = _run(session)
    recorder = SpanRecorder(session, run.id, sensitive_values=("secret-value",))

    processing = recorder.start(
        "query_processing",
        "Process query",
        input_summary={"user_input": "What is measured?"},
        configuration_snapshot={"api_key": "secret-value", "enabled": True},
    )
    classification = recorder.start(
        "classification",
        "Classify query",
        parent=processing,
        configuration_snapshot={"provider_token": "secret-value"},
    )
    classification.succeed({"category": "direct_fact", "confidence": 0.9})
    processing.succeed({"rewritten": False}, artifact_ids=[uuid4()])

    spans = session.scalars(
        select(TraceSpan)
        .where(TraceSpan.query_run_id == run.id)
        .order_by(TraceSpan.sequence_number)
    ).all()
    assert [span.span_type for span in spans] == ["query_processing", "classification"]
    assert [span.sequence_number for span in spans] == [1, 2]
    assert spans[1].parent_span_id == spans[0].id
    assert all(span.status == TraceSpanStatus.SUCCEEDED for span in spans)
    assert all(span.finished_at is not None for span in spans)
    assert all(span.latency_ms is not None and span.latency_ms >= 0 for span in spans)
    assert spans[0].configuration_snapshot["api_key"] == REDACTED
    assert spans[1].configuration_snapshot["provider_token"] == REDACTED
    assert len(spans[0].artifact_ids) == 1


def test_context_manager_records_failed_span_without_exception_payload(
    session: Session,
) -> None:
    run = _run(session)
    recorder = SpanRecorder(session, run.id)
    prior = recorder.start("routing", "Configured route")
    prior.succeed({"mode": "hybrid"})

    with pytest.raises(RuntimeError, match="provider leaked-text"):
        with recorder.span("generation", "Generate answer"):
            raise RuntimeError("provider leaked-text")

    spans = session.scalars(
        select(TraceSpan)
        .where(TraceSpan.query_run_id == run.id)
        .order_by(TraceSpan.sequence_number)
    ).all()
    assert spans[0].status == TraceSpanStatus.SUCCEEDED
    span = spans[1]
    assert span.status == TraceSpanStatus.FAILED
    assert span.error_code == "UNEXPECTED_STAGE_FAILURE"
    assert span.output_summary == {
        "exception_type": "RuntimeError",
        "exception_present": True,
    }
    assert "leaked-text" not in json.dumps(span.output_summary)


def test_span_cannot_be_finalized_twice(session: Session) -> None:
    run = _run(session)
    handle = SpanRecorder(session, run.id).start("routing", "Configured route")
    handle.succeed()
    with pytest.raises(TraceRecordingError):
        handle.fail("ROUTE_UNAVAILABLE")


def test_trace_summary_and_versioned_export_do_not_read_or_leak_artifact_payloads(
    session: Session,
) -> None:
    run = _run(session)
    run.status = QueryRunStatus.SUCCEEDED
    run.total_latency_ms = 123
    run.input_tokens = 40
    run.output_tokens = 10
    run.estimated_cost = None
    document = SourceDocument(
        corpus_version_id=run.corpus_version_id,
        title="Source",
        file_hash="b" * 64,
        mime_type="text/plain",
    )
    session.add(document)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        corpus_version_id=run.corpus_version_id,
        chunker_id="test",
        sequence_number=0,
        text="Observable source passage",
        token_count=3,
        content_hash="c" * 64,
    )
    session.add(chunk)
    session.flush()
    session.add_all(
        [
            RetrievalResultRecord(
                query_run_id=run.id,
                chunk_id=chunk.id,
                retriever_type="hybrid",
                original_rank=2,
                fused_rank=2,
                reranked_rank=1,
                selected_for_context=True,
            ),
            ContextSource(
                query_run_id=run.id,
                chunk_id=chunk.id,
                document_id=document.id,
                citation_id="S1",
                sequence_number=1,
                selected=True,
                token_count=3,
            ),
            GeneratedClaim(
                query_run_id=run.id,
                sequence_number=1,
                claim_text="A claim",
                citation_ids=["S1"],
                support_status=ClaimSupportStatus.NOT_EVALUATED,
            ),
        ]
    )
    artifact = Artifact(
        corpus_version_id=run.corpus_version_id,
        artifact_type="generator-context",
        content_hash="d" * 64,
        media_type="text/plain",
        producing_operation="query-context-build",
        configuration={"api_key": "export-secret", "token_budget": 500},
        storage_key="00/export-test",
        size_bytes=999,
    )
    session.add(artifact)
    session.flush()
    run.context_artifact_id = artifact.id

    recorder = SpanRecorder(session, run.id, sensitive_values=("export-secret",))
    retrieval = recorder.start("retrieval", "Retrieve", configuration_snapshot={})
    retrieval.succeed({"candidate_count": 1})
    context = recorder.start("context_construction", "Build context")
    context.succeed(
        {"selected_count": 1, "exact_context": "stored as artifact"},
        artifact_ids=[artifact.id],
    )

    summary = build_trace_summary(session, run.id)
    exported = export_observable_trace(
        session, run.id, sensitive_values=("export-secret",)
    )
    payload = exported.model_dump(mode="json")

    assert summary.retrieval_candidate_count == 1
    assert summary.reranked_candidate_count == 1
    assert summary.selected_context_count == 1
    assert summary.rank_movement[0].movement == 1
    assert summary.not_evaluated_claim_count == 1
    assert payload["schema_version"] == "ragscope.observable-trace.v1"
    assert [span["sequence_number"] for span in payload["spans"]] == [1, 2]
    assert payload["artifacts"][0]["content_hash"] == "d" * 64
    assert payload["artifacts"][0]["query_run_id"] == str(run.id)
    assert payload["artifacts"][0]["trace_span_id"] == str(context.id)
    assert payload["artifacts"][0]["configuration"]["api_key"] == REDACTED
    assert "export-secret" not in json.dumps(payload)
    # Export exposes immutable metadata, not a large artifact body.
    assert "Observable source passage" not in json.dumps(payload)
