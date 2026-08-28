"""Typed background job handlers.

The database payload contains only stable identifiers and validated configuration
snapshots. Provider credentials are resolved from Settings inside the worker.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.analysis.contracts import DenominatorPolicy
from backend.app.analysis.service import ExperimentAnalysisService
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import Settings
from backend.app.datasets.schemas import DatasetExtractionRequest
from backend.app.datasets.service import DatasetExtractionService
from backend.app.datasets.strategies import DeterministicDatasetGenerationProvider
from backend.app.db.models import Artifact, CorpusVersion, Job, SourceDocument
from backend.app.documents.chunkers import (
    FixedTokenChunker,
    FixedTokenConfiguration,
    StructureAwareChunker,
    StructureAwareConfiguration,
)
from backend.app.documents.service import ChunkService, DocumentService
from backend.app.evaluation.service import EvaluationService
from backend.app.experiments.repository import (
    SQLAlchemyExperimentDependencyResolver,
    SQLAlchemyExperimentStore,
)
from backend.app.experiments.schemas import ExperimentRunCell, QueryExecutionOutcome
from backend.app.experiments.service import ExperimentService
from backend.app.indexing.service import IndexingService
from backend.app.jobs.service import cancellation_requested, heartbeat_job
from backend.app.providers.registry import create_embedding_provider, create_generation_provider
from backend.app.query_runtime.schemas import QueryRunCreate
from backend.app.query_runtime.service import QueryOrchestrator
from backend.app.research.contracts import ExactMetric, ResearchFigureConfiguration
from backend.app.research.figures import generate_required_figures


def execute_job(
    session: Session,
    job: Job,
    *,
    settings: Settings,
    artifact_store: LocalArtifactStore,
) -> list[str]:
    handlers = {
        "document_parsing": _parse_document,
        "chunk_generation": _generate_chunks,
        "build_indexes": _build_indexes,
        "dataset_extraction": _extract_dataset,
        "query_evaluation": _evaluate_query,
        "experiment-execution": _execute_experiment,
    "experiment_export": _export_experiment,
    "research_figure_generation": _generate_research_figures,
    }
    handler = handlers.get(job.job_type)
    if handler is None:
        raise ValueError(f"No worker handler is registered for job type {job.job_type!r}")
    return handler(session, job, settings, artifact_store)


def _parse_document(
    session: Session, job: Job, settings: Settings, store: LocalArtifactStore
) -> list[str]:
    document = session.get(SourceDocument, UUID(str(job.input_reference["document_id"])))
    if document is None:
        raise LookupError("Document not found")
    DocumentService(session, store, max_upload_bytes=settings.max_upload_bytes).parse(document)
    return [
        str(value)
        for value in session.scalars(
            select(Artifact.id).where(
                Artifact.document_id == document.id,
                Artifact.artifact_type == "normalized-document",
            )
        )
    ]


def _generate_chunks(
    session: Session, job: Job, _settings: Settings, _store: LocalArtifactStore
) -> list[str]:
    version = session.get(
        CorpusVersion, UUID(str(job.input_reference["corpus_version_id"]))
    )
    if version is None:
        raise LookupError("Corpus version not found")
    configuration = dict(job.input_reference.get("configuration", {}))
    strategy = str(job.input_reference.get("strategy", "structure-aware"))
    if strategy == "fixed":
        fixed = FixedTokenConfiguration(
            target_tokens=int(configuration.get("target_tokens", 384)),
            overlap_tokens=int(configuration.get("overlap_tokens", 32)),
            include_section_titles=bool(
                configuration.get("include_section_titles", True)
            ),
        )
        chunker: FixedTokenChunker | StructureAwareChunker = FixedTokenChunker(fixed)
    else:
        structure = StructureAwareConfiguration(
            target_tokens=int(configuration.get("target_tokens", 384)),
            include_section_titles=bool(
                configuration.get("include_section_titles", True)
            ),
            preserve_tables=bool(configuration.get("preserve_tables", True)),
        )
        chunker = StructureAwareChunker(structure)
    chunks = ChunkService(session).generate_for_version(version, chunker)
    version.chunker_configuration = {
        "chunker_id": chunker.chunker_id,
        "configuration": configuration,
    }
    job.progress_current = len(chunks)
    return []


def _build_indexes(
    session: Session, job: Job, settings: Settings, _store: LocalArtifactStore
) -> list[str]:
    version = session.get(
        CorpusVersion, UUID(str(job.input_reference["corpus_version_id"]))
    )
    if version is None:
        raise LookupError("Corpus version not found")
    provider = create_embedding_provider(version.embedding_configuration, settings=settings)
    lexical, dense = IndexingService(session, provider).build_all(version.id)
    job.progress_current = 2
    return [str(lexical.id), str(dense.id)]


def _evaluate_query(
    session: Session, job: Job, _settings: Settings, _store: LocalArtifactStore
) -> list[str]:
    run_id = UUID(str(job.input_reference["query_run_id"]))
    configured_versions = job.input_reference.get("metric_versions", [])
    EvaluationService(session).evaluate(
        run_id,
        metric_versions=(
            {str(value) for value in configured_versions}
            if configured_versions
            else None
        ),
    )
    job.progress_current = 1
    return []


def _extract_dataset(
    session: Session, job: Job, settings: Settings, store: LocalArtifactStore
) -> list[str]:
    document_id = UUID(str(job.input_reference["document_id"]))
    request = DatasetExtractionRequest.model_validate(
        job.input_reference.get("configuration", {})
    )
    provider = (
        DeterministicDatasetGenerationProvider()
        if request.provider == "fake"
        else create_generation_provider(
            {
                "provider": request.provider,
                **({"model": request.model} if request.model is not None else {}),
            },
            settings=settings,
        )
    )
    record = DatasetExtractionService(session, store, provider).extract(
        document_id, request, job=job
    )
    job.progress_current = 1
    return [
        str(value)
        for value in (
            record.raw_response_artifact_id,
            record.structured_result_artifact_id,
        )
        if value is not None
    ]


def _execute_experiment(
    session: Session, job: Job, settings: Settings, _store: LocalArtifactStore
) -> list[str]:
    experiment_id = UUID(str(job.input_reference["experiment_id"]))
    operation = str(job.input_reference.get("operation", "start"))
    store = SQLAlchemyExperimentStore(session)
    service = ExperimentService(
        store,
        SQLAlchemyExperimentDependencyResolver(session, settings=settings),
        experiment_cost_limit=settings.experiment_cost_limit,
    )
    execute = _query_executor(session)

    def should_pause() -> bool:
        session.refresh(job)
        if job.lease_owner is not None:
            heartbeat_job(job, worker_id=job.lease_owner, lease_seconds=600)
            session.flush()
        return cancellation_requested(job)

    report = (
        service.resume(experiment_id, execute=execute, should_pause=should_pause)
        if operation == "resume"
        else service.start(experiment_id, execute=execute, should_pause=should_pause)
    )
    job.progress_total = report.progress.total
    job.progress_current = report.progress.succeeded + report.progress.failed
    return []


def _query_executor(session: Session):  # type: ignore[no-untyped-def]
    orchestrator = QueryOrchestrator(session)

    def execute(
        cell: ExperimentRunCell, request: QueryRunCreate
    ) -> QueryExecutionOutcome:
        run = orchestrator.execute(
            request,
            raise_on_failure=False,
            experiment_run_id=cell.id,
            experiment_attempt_number=cell.attempt_count,
        )
        EvaluationService(session).evaluate(run.id)
        session.commit()
        return QueryExecutionOutcome(
            query_run_id=run.id,
            succeeded=run.status.value == "succeeded",
            failure_code=run.failure_code,
            failure_message=run.failure_message,
        )

    return execute


def _export_experiment(
    session: Session, job: Job, _settings: Settings, store: LocalArtifactStore
) -> list[str]:
    experiment_id = UUID(str(job.input_reference["experiment_id"]))
    bundle = ExperimentAnalysisService(session).export_bundle(experiment_id)
    audit = ExperimentAnalysisService(session).research_audit_manifest(experiment_id)
    analysis_service = ExperimentAnalysisService(session)
    result = analysis_service.results(experiment_id, limit=1000)
    document = json.loads(bundle.json_document)
    document["research_audit"] = audit
    document["adaptive_analysis"] = result.adaptive_analysis
    outputs: list[tuple[str, bytes, str]] = [
        ("experiment-runs.csv", bundle.run_metrics_csv.encode(), "text/csv"),
        ("experiment-aggregates.csv", bundle.aggregate_metrics_csv.encode(), "text/csv"),
        (
            "experiment-raw-manifest.json",
            json.dumps(audit, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        ),
        (
            "experiment-adaptive-analysis.json",
            json.dumps(
                result.adaptive_analysis,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            "application/json",
        ),
        (
            "experiment-analysis.json",
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
            "application/json",
        ),
    ]
    figures = generate_required_figures(
        analysis_service.all_runs(experiment_id), _figure_configuration()
    )
    figure_manifest: list[dict[str, object]] = []
    for figure in figures:
        spec_name = f"figure-{figure.spec.figure_id}.json"
        svg_name = f"figure-{figure.spec.figure_id}.svg"
        outputs.extend(
            (
                (
                    spec_name,
                    json.dumps(asdict(figure.spec), sort_keys=True, separators=(",", ":")).encode(),
                    "application/json",
                ),
                (svg_name, figure.svg.encode(), "image/svg+xml"),
            )
        )
        figure_manifest.append(
            {
                "figure_id": figure.spec.figure_id,
                "status": figure.spec.status,
                "json_artifact_type": spec_name,
                "svg_artifact_type": svg_name,
            }
        )
    outputs.append(
        (
            "research-figures-manifest.json",
            json.dumps(
                {"schema_version": "ragscope-research-figures.v1", "figures": figure_manifest},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            "application/json",
        )
    )
    artifact_ids: list[str] = []
    for artifact_type, content, media_type in outputs:
        descriptor = store.put_bytes(
            content,
            media_type=media_type,
            producing_operation="experiment-export",
            original_filename=artifact_type,
            configuration={"experiment_id": str(experiment_id), "schema_version": "v1"},
        )
        row = Artifact(
            id=descriptor.id,
            experiment_id=experiment_id,
            job_id=job.id,
            artifact_type=artifact_type,
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=descriptor.original_filename,
            producing_operation=descriptor.producing_operation,
            producer_version="experiment-export-v1",
            configuration=descriptor.configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )
        session.add(row)
        artifact_ids.append(str(row.id))
    job.progress_current = len(artifact_ids)
    job.progress_total = len(artifact_ids)
    return artifact_ids


def _generate_research_figures(
    session: Session, job: Job, settings: Settings, store: LocalArtifactStore
) -> list[str]:
    return _export_experiment(session, job, settings, store)


def _figure_configuration() -> ResearchFigureConfiguration:
    quality = DenominatorPolicy(
        True, "non-infrastructure runs with the exact stored quality metric"
    )
    operational = DenominatorPolicy(False, "all runs with the exact stored operational metric")
    return ResearchFigureConfiguration(
        retrieval_recall=ExactMetric(
            "recall_at_k",
            "retrieval-evidence-v1",
            "retrieval",
            "deterministic_human_benchmark_evidence",
            quality,
        ),
        answer_correctness=ExactMetric(
            "answer_correctness", "generation-quality-v1", "generation", "human", quality
        ),
        citation_support_rate=ExactMetric(
            "claim_support_rate", "citation-metrics-v1", "citation", "automated", quality
        ),
        estimated_cost=ExactMetric(
            "estimated_cost", "operational-metrics.v1", "cost", "operational", operational
        ),
        total_latency=ExactMetric(
            "total_latency_ms",
            "operational-metrics.v1",
            "overall",
            "operational",
            operational,
        ),
        evidence_survival_version="evidence-survival.v1",
        evidence_survival_policy=quality,
        failure_stage_dimension="failure_stage",
    )
