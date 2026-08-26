from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Artifact,
    CorpusVersion,
    PipelineConfiguration,
    QueryRun,
    TraceSpan,
)

from .redaction import configured_sensitive_values, redact
from .schemas import (
    ArtifactReference,
    ObservableTraceExport,
    TraceRunSnapshot,
    TraceSpanRead,
)
from .summary import build_trace_summary


def export_observable_trace(
    session: Session,
    query_run_id: UUID,
    *,
    sensitive_values: tuple[str, ...] | None = None,
) -> ObservableTraceExport:
    """Return a versioned, secret-free reconstruction of observable execution."""

    run = session.get(QueryRun, query_run_id)
    if run is None:
        raise LookupError("Query run does not exist")
    pipeline = session.get(PipelineConfiguration, run.pipeline_configuration_id)
    if pipeline is None:  # pragma: no cover - protected by the database foreign key
        raise LookupError("Pipeline configuration does not exist")
    corpus_version = session.get(CorpusVersion, run.corpus_version_id)
    if corpus_version is None:  # pragma: no cover - protected by the database foreign key
        raise LookupError("Corpus version does not exist")

    secrets = tuple(configured_sensitive_values()) if sensitive_values is None else sensitive_values
    spans = session.scalars(
        select(TraceSpan)
        .where(TraceSpan.query_run_id == query_run_id)
        .order_by(TraceSpan.sequence_number)
    ).all()
    safe_spans = [
        TraceSpanRead(
            id=span.id,
            query_run_id=span.query_run_id,
            parent_span_id=span.parent_span_id,
            sequence_number=span.sequence_number,
            span_type=span.span_type,
            name=span.name,
            status=_enum_value(span.status),
            started_at=span.started_at,
            finished_at=span.finished_at,
            latency_ms=span.latency_ms,
            input_summary=_dict(redact(span.input_summary, sensitive_values=secrets)),
            output_summary=_dict(redact(span.output_summary, sensitive_values=secrets)),
            configuration_snapshot=_dict(
                redact(span.configuration_snapshot, sensitive_values=secrets)
            ),
            error_code=span.error_code,
            artifact_ids=[str(item) for item in span.artifact_ids],
        )
        for span in spans
    ]

    artifact_ids: set[UUID] = {
        UUID(str(item))
        for span in spans
        for item in span.artifact_ids
        if _is_uuid(item)
    }
    artifact_ids.update(
        item for item in (run.context_artifact_id, run.raw_response_artifact_id) if item
    )
    artifacts = session.scalars(
        select(Artifact)
        .where(
            (Artifact.query_run_id == query_run_id)
            | (Artifact.id.in_(artifact_ids) if artifact_ids else False)
        )
        .order_by(Artifact.id)
    ).all()

    return ObservableTraceExport(
        exported_at=datetime.now(UTC),
        run=TraceRunSnapshot(
            id=run.id,
            corpus_version_id=run.corpus_version_id,
            corpus_version=_dict(
                redact(_corpus_snapshot(corpus_version), sensitive_values=secrets)
            ),
            pipeline_configuration_id=run.pipeline_configuration_id,
            prompt_template_id=run.prompt_template_id,
            status=_enum_value(run.status),
            original_query=run.query_text,
            normalized_query=run.normalized_query,
            rewritten_query=run.rewritten_query,
            classification=_dict(redact(run.classification, sensitive_values=secrets)),
            configured_route=_dict(redact(run.route_decision, sensitive_values=secrets)),
            answerability=(
                _enum_value(run.answerability_decision)
                if run.answerability_decision is not None
                else None
            ),
            failure_code=run.failure_code,
        ),
        pipeline_configuration=_dict(
            redact(_pipeline_snapshot(pipeline), sensitive_values=secrets)
        ),
        prompt=(
            {
                "id": str(run.prompt_template.id),
                "prompt_id": run.prompt_template.prompt_id,
                "version": run.prompt_template.version,
                "content_hash": run.prompt_template.content_hash,
            }
            if run.prompt_template is not None
            else None
        ),
        spans=safe_spans,
        artifacts=[
            ArtifactReference(
                id=artifact.id,
                query_run_id=artifact.query_run_id,
                trace_span_id=artifact.trace_span_id,
                artifact_type=artifact.artifact_type,
                content_hash=artifact.content_hash,
                media_type=artifact.media_type,
                producing_operation=artifact.producing_operation,
                producer_version=artifact.producer_version,
                configuration=_dict(
                    redact(artifact.configuration, sensitive_values=secrets)
                ),
                size_bytes=artifact.size_bytes,
            )
            for artifact in artifacts
        ],
        summary=build_trace_summary(session, query_run_id),
    )


def _pipeline_snapshot(pipeline: PipelineConfiguration) -> dict[str, Any]:
    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "version": pipeline.version,
        "configuration_hash": pipeline.configuration_hash,
        "frozen_at": pipeline.frozen_at,
        "execution_mode": _enum_value(pipeline.execution_mode),
        "router_configuration_id": (
            str(pipeline.router_configuration_id)
            if pipeline.router_configuration_id is not None
            else None
        ),
        "adaptive": pipeline.adaptive_configuration,
        "retrieval_mode": _enum_value(pipeline.retrieval_mode),
        "lexical": pipeline.lexical_configuration,
        "dense": pipeline.dense_configuration,
        "fusion": pipeline.fusion_configuration,
        "reranker": pipeline.reranker_configuration,
        "query_processing": pipeline.query_processing_configuration,
        "context": pipeline.context_configuration,
        "generation": pipeline.generation_configuration,
        "citation": pipeline.citation_configuration,
        "prompt_versions": pipeline.prompt_versions,
    }


def _corpus_snapshot(version: CorpusVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "corpus_id": str(version.corpus_id),
        "version_label": version.version_label,
        "status": _enum_value(version.status),
        "content_hash": version.content_hash,
        "frozen_at": version.frozen_at,
        "parser_configuration": version.parser_configuration,
        "chunker_configuration": version.chunker_configuration,
        "embedding_configuration": version.embedding_configuration,
    }


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):  # pragma: no cover - internal type invariant
        raise TypeError("Expected a JSON object")
    return value


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _is_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True
