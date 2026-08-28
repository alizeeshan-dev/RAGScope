"""Idempotent deterministic end-to-end research fixture.

This command intentionally uses only local fake providers. It is safe for CI and never
reads Gemini credentials. It creates a small evidence-rich corpus, P0-P5, five reviewed
fixture questions, one frozen experiment, immutable exports, and all eight figure artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adaptive.schemas import RouterConfigurationCreate
from backend.app.adaptive.service import (
    create_router_configuration,
    freeze_router_configuration,
)
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.benchmarks.schemas import (
    AnnotationStatus,
    BenchmarkCreate,
    BenchmarkQuestionCreate,
    BenchmarkQuestionUpdate,
    BenchmarkVersionCreate,
    Difficulty,
    EvidenceReferenceCreate,
    EvidenceSetCreate,
    QuestionType,
)
from backend.app.benchmarks.service import BenchmarkService
from backend.app.core.config import Settings, get_settings
from backend.app.corpora.service import freeze_version
from backend.app.db.models import (
    Artifact,
    Benchmark,
    BenchmarkQuestion,
    BenchmarkVersion,
    Corpus,
    CorpusVersion,
    Experiment,
    ExperimentStatus,
    Job,
    JobStatus,
    PipelineConfiguration,
    PipelineExecutionMode,
    RetrievalMode,
    RouterConfiguration,
    SourceDocument,
)
from backend.app.db.session import SessionLocal
from backend.app.documents.chunkers import (
    FixedTokenChunker,
    FixedTokenConfiguration,
    StructureAwareChunker,
    StructureAwareConfiguration,
)
from backend.app.documents.service import ChunkService, DocumentService
from backend.app.experiments.repository import (
    SQLAlchemyExperimentDependencyResolver,
    SQLAlchemyExperimentStore,
)
from backend.app.experiments.schemas import (
    ExperimentAnalysisConfiguration,
    ExperimentCreate,
)
from backend.app.experiments.service import ExperimentService
from backend.app.indexing.service import IndexingService
from backend.app.jobs.handlers import execute_job
from backend.app.jobs.service import complete_job, create_job, start_job
from backend.app.pipelines.schemas import PipelineConfigurationCreate
from backend.app.pipelines.service import (
    create_pipeline_configuration,
    freeze_pipeline_configuration,
)
from backend.app.providers.fake import DeterministicEmbeddingProvider

FIXTURE_CORPUS = "RAGScope deterministic fixture corpus"
FIXTURE_BENCHMARK = "RAGScope deterministic fixture benchmark"
FIXTURE_EXPERIMENT = "RAGScope deterministic fixture experiment"
FIXTURE_VERSION = "fixture-v1"
FIXTURE_CODE_COMMIT = "fixture-source-controlled-v1"


def run_fixture(settings: Settings | None = None) -> dict[str, object]:
    active_settings = settings or get_settings()
    artifact_store = LocalArtifactStore(active_settings.artifact_root)
    fixture_root = Path(__file__).resolve().parents[2] / "benchmark" / "fixtures" / "synthetic"
    with SessionLocal() as session:
        corpus, version = _corpus(session, artifact_store, active_settings, fixture_root)
        benchmark, benchmark_version = _benchmark(session, version)
        router = _router(session)
        pipelines = _pipelines(session, router)
        experiment = _experiment(
            session,
            active_settings,
            version,
            benchmark_version,
            pipelines,
        )
        export_job = _export(session, active_settings, artifact_store, experiment)
        artifacts = list(
            session.scalars(
                select(Artifact)
                .where(Artifact.experiment_id == experiment.id)
                .order_by(Artifact.artifact_type, Artifact.created_at, Artifact.id)
            )
        )
        result: dict[str, object] = {
            "fixture_schema_version": "ragscope-deterministic-fixture.v1",
            "corpus_id": str(corpus.id),
            "corpus_version_id": str(version.id),
            "benchmark_id": str(benchmark.id),
            "benchmark_version_id": str(benchmark_version.id),
            "router_configuration_id": str(router.id),
            "pipeline_configuration_ids": [str(value.id) for value in pipelines],
            "experiment_id": str(experiment.id),
            "experiment_status": experiment.status.value,
            "export_job_id": str(export_job.id),
            "artifact_root": str(active_settings.artifact_root),
            "export_artifacts": [
                {
                    "id": str(value.id),
                    "type": value.artifact_type,
                    "content_hash": value.content_hash,
                }
                for value in artifacts
            ],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return result


def _corpus(
    session: Session,
    artifact_store: LocalArtifactStore,
    settings: Settings,
    fixture_root: Path,
) -> tuple[Corpus, CorpusVersion]:
    corpus = session.scalar(select(Corpus).where(Corpus.name == FIXTURE_CORPUS))
    if corpus is None:
        corpus = Corpus(
            name=FIXTURE_CORPUS,
            description="Synthetic evidence, contradictions, tables, and distractors for CI.",
            domain="deterministic-fixture",
        )
        session.add(corpus)
        session.flush()
    version = session.scalar(
        select(CorpusVersion).where(
            CorpusVersion.corpus_id == corpus.id,
            CorpusVersion.version_label == FIXTURE_VERSION,
        )
    )
    if version is None:
        version = CorpusVersion(
            corpus_id=corpus.id,
            version_label=FIXTURE_VERSION,
            parser_configuration={"parser_id": "markdown-parser", "version": "1"},
            chunker_configuration={},
            embedding_configuration={
                "provider": "fake",
                "model": "fake-hash-embedding-v1",
                "dimension": 64,
                "preprocessing_version": "hashed-bow-v1",
            },
        )
        session.add(version)
        session.flush()
    if version.is_frozen:
        return corpus, version

    documents = {
        value.title: value
        for value in session.scalars(
            select(SourceDocument).where(SourceDocument.corpus_version_id == version.id)
        )
    }
    document_service = DocumentService(
        session, artifact_store, max_upload_bytes=settings.max_upload_bytes
    )
    for source_path in sorted(fixture_root.glob("*.md")):
        title = source_path.stem
        document = documents.get(title)
        if document is None:
            document = document_service.upload(
                corpus_version_id=version.id,
                filename=source_path.name,
                content=source_path.read_bytes(),
                claimed_media_type="text/markdown",
                title=title,
            )
            document.source_uri = f"fixture://{source_path.name}"
            document.license_information = "Synthetic fixture; repository test data"
        if document.parse_status.value != "ready":
            document_service.parse(document)

    chunk_service = ChunkService(session)
    chunk_service.generate_for_version(
        version,
        FixedTokenChunker(
            FixedTokenConfiguration(target_tokens=48, overlap_tokens=8)
        ),
    )
    structure = StructureAwareChunker(
        StructureAwareConfiguration(
            target_tokens=80,
            include_section_titles=True,
            preserve_tables=True,
        )
    )
    chunk_service.generate_for_version(version, structure)
    version.chunker_configuration = {
        "chunker_id": structure.chunker_id,
        "configuration": {
            "target_tokens": 80,
            "include_section_titles": True,
            "preserve_tables": True,
        },
    }
    session.flush()
    IndexingService(
        session,
        DeterministicEmbeddingProvider(
            dimension=64, model_id="fake-hash-embedding-v1"
        ),
    ).build_all(version.id)
    freeze_version(session, version)
    session.commit()
    return corpus, version


def _benchmark(
    session: Session, version: CorpusVersion
) -> tuple[Benchmark, BenchmarkVersion]:
    service = BenchmarkService(session)
    benchmark = session.scalar(select(Benchmark).where(Benchmark.name == FIXTURE_BENCHMARK))
    if benchmark is None:
        created_benchmark = service.create_benchmark(
            BenchmarkCreate(
                name=FIXTURE_BENCHMARK,
                description="Five source-controlled human-labelled fixture questions.",
            )
        )
        benchmark = session.get(Benchmark, created_benchmark.id)
        assert benchmark is not None
    benchmark_version = session.scalar(
        select(BenchmarkVersion).where(
            BenchmarkVersion.benchmark_id == benchmark.id,
            BenchmarkVersion.corpus_version_id == version.id,
            BenchmarkVersion.version == 1,
        )
    )
    if benchmark_version is None:
        created_version = service.create_version(
            benchmark.id,
            BenchmarkVersionCreate(
                corpus_version_id=version.id,
                version=1,
                notes="Source-controlled deterministic fixture annotations.",
            ),
        )
        benchmark_version = session.get(BenchmarkVersion, created_version.id)
        assert benchmark_version is not None
    if benchmark_version.frozen_at is not None:
        return benchmark, benchmark_version

    atlas = _source(session, version.id, "atlas")
    river = _source(session, version.id, "river")
    definitions = (
        (
            "How many annotated images does the Atlas Vision Dataset contain?",
            QuestionType.DIRECT_FACT_LOOKUP,
            Difficulty.EASY,
            "Atlas contains 10,000 annotated images.",
            "State the final dataset-card image count.",
            ((_passage(atlas, "exactly 10,000 annotated wildlife images"),),),
        ),
        (
            "How many images are in the Atlas test split?",
            QuestionType.TABLE_BASED,
            Difficulty.EASY,
            "The Atlas test split has 2,000 images.",
            "Read the Test row rather than the total.",
            ((_passage(atlas, "| Test | 2,000 | 20 |"),),),
        ),
        (
            "Compare the licenses of Atlas and RiverSound.",
            QuestionType.DATASET_COMPARISON,
            Difficulty.MEDIUM,
            "Both use CC BY 4.0 (RiverSound spells out the same license).",
            "Identify the license for both datasets.",
            (
                (
                    _passage(atlas, "CC BY 4.0 license"),
                    _passage(river, "Creative Commons Attribution 4.0 license"),
                ),
            ),
        ),
        (
            "Why do Atlas sources report both 9,500 and 10,000 images?",
            QuestionType.CONTRADICTORY_SOURCE,
            Difficulty.HARD,
            (
                "The early preprint reported usable images after exclusions; "
                "the final card reports the final total."
            ),
            "Resolve the apparent conflict by dataset version/source status.",
            (
                (
                    _passage(atlas, "exactly 10,000 annotated wildlife images"),
                    _passage(river, "early Atlas preprint reported 9,500 usable images"),
                ),
            ),
        ),
    )
    existing = {
        value.question_text: value
        for value in session.scalars(
            select(BenchmarkQuestion).where(
                BenchmarkQuestion.benchmark_version_id == benchmark_version.id
            )
        )
    }
    for text, question_type, difficulty, answer, criteria, evidence_sets in definitions:
        question = existing.get(text)
        if question is None:
            created = service.create_question(
                benchmark_version.id,
                BenchmarkQuestionCreate(
                    question_text=text,
                    question_type=question_type,
                    difficulty=difficulty,
                    answerable=True,
                    reference_answer=answer,
                    answer_criteria=criteria,
                    annotation_status=AnnotationStatus.DRAFT,
                    annotation_notes="Reviewed source-controlled fixture annotation.",
                ),
            )
            question = session.get(BenchmarkQuestion, created.id)
            assert question is not None
        if not question.evidence_sets:
            for set_number, references in enumerate(evidence_sets, start=1):
                service.add_evidence_set(
                    question.id,
                    EvidenceSetCreate(
                        set_number=set_number,
                        description="Complete acceptable fixture evidence.",
                        references=list(references),
                    ),
                )
        if question.annotation_status.value != "reviewed":
            service.update_question(
                question.id,
                BenchmarkQuestionUpdate(annotation_status=AnnotationStatus.REVIEWED),
            )

    unanswerable_text = "What was the acquisition price paid for RiverSound?"
    if unanswerable_text not in existing:
        service.create_question(
            benchmark_version.id,
            BenchmarkQuestionCreate(
                question_text=unanswerable_text,
                question_type=QuestionType.UNANSWERABLE,
                difficulty=Difficulty.MEDIUM,
                answerable=False,
                unanswerable_explanation=(
                    "No acquisition price is stated in either fixture source."
                ),
                annotation_status=AnnotationStatus.REVIEWED,
                annotation_notes="Reviewed negative fixture annotation.",
            ),
        )
    service.freeze_version(benchmark_version.id)
    session.commit()
    refreshed = session.get(BenchmarkVersion, benchmark_version.id)
    assert refreshed is not None
    return benchmark, refreshed


def _source(session: Session, version_id: UUID, title: str) -> SourceDocument:
    document = session.scalar(
        select(SourceDocument).where(
            SourceDocument.corpus_version_id == version_id,
            SourceDocument.title == title,
        )
    )
    if document is None:
        raise RuntimeError(f"Fixture document is missing: {title}")
    return document


def _passage(document: SourceDocument, phrase: str) -> EvidenceReferenceCreate:
    element = next((value for value in document.elements if phrase in value.text), None)
    if element is None:
        raise RuntimeError(f"Fixture phrase is missing from {document.title}: {phrase}")
    chunk = next(
        (
            value
            for value in document.chunks
            if str(element.id) in value.source_element_ids and phrase in value.text
        ),
        None,
    )
    if chunk is None:
        raise RuntimeError(f"Fixture chunk is missing for phrase: {phrase}")
    start = element.text.index(phrase)
    return EvidenceReferenceCreate(
        document_id=document.id,
        page_number=element.page_number,
        element_id=element.id,
        chunk_id=chunk.id,
        selected_text=phrase,
        start_offset=start,
        end_offset=start + len(phrase),
    )


def _router(session: Session) -> RouterConfiguration:
    row = session.scalar(
        select(RouterConfiguration).where(
            RouterConfiguration.name == "Fixture deterministic router",
            RouterConfiguration.version == 1,
        )
    )
    if row is None:
        row = create_router_configuration(
            session,
            RouterConfigurationCreate(name="Fixture deterministic router", version=1),
        )
    if not row.is_frozen:
        freeze_router_configuration(session, row)
    session.commit()
    return row


def _pipelines(
    session: Session, router: RouterConfiguration
) -> tuple[PipelineConfiguration, ...]:
    definitions = (
        ("P0 Fixture No RAG", RetrievalMode.NONE, False, PipelineExecutionMode.FIXED),
        ("P1 Fixture Lexical", RetrievalMode.LEXICAL, False, PipelineExecutionMode.FIXED),
        ("P2 Fixture Dense", RetrievalMode.DENSE, False, PipelineExecutionMode.FIXED),
        ("P3 Fixture Hybrid", RetrievalMode.HYBRID, False, PipelineExecutionMode.FIXED),
        ("P4 Fixture Hybrid Rerank", RetrievalMode.HYBRID, True, PipelineExecutionMode.FIXED),
        ("P5 Fixture Adaptive", RetrievalMode.HYBRID, True, PipelineExecutionMode.ADAPTIVE),
    )
    output: list[PipelineConfiguration] = []
    for name, retrieval_mode, reranking, execution_mode in definitions:
        row = session.scalar(
            select(PipelineConfiguration).where(
                PipelineConfiguration.name == name,
                PipelineConfiguration.version == 1,
            )
        )
        if row is None:
            row = create_pipeline_configuration(
                session,
                PipelineConfigurationCreate(
                    name=name,
                    version=1,
                    execution_mode=execution_mode,
                    router_configuration_id=(
                        router.id
                        if execution_mode is PipelineExecutionMode.ADAPTIVE
                        else None
                    ),
                    retrieval_mode=retrieval_mode,
                    lexical_configuration={"top_k": 5, "candidate_count": 10},
                    dense_configuration={"top_k": 5, "candidate_count": 10},
                    fusion_configuration={"rrf_k": 60, "final_count": 5},
                    reranker_configuration={
                        "enabled": reranking,
                        "provider": "fake",
                        "model": "fake-token-overlap-reranker-v1",
                        "input_candidate_count": 10,
                        "final_count": 5,
                    },
                    context_configuration={
                        "token_budget": 512,
                        "deduplicate": True,
                        "overlap_threshold": 0.85,
                    },
                    generation_configuration={
                        "provider": "fake",
                        "model": "fake-generation-v1",
                        "temperature": 0,
                        "max_output_tokens": 128,
                    },
                ),
            )
        if not row.is_frozen:
            freeze_pipeline_configuration(session, row)
        output.append(row)
    session.commit()
    return tuple(output)


def _experiment(
    session: Session,
    settings: Settings,
    version: CorpusVersion,
    benchmark: BenchmarkVersion,
    pipelines: tuple[PipelineConfiguration, ...],
) -> Experiment:
    existing = session.scalar(select(Experiment).where(Experiment.name == FIXTURE_EXPERIMENT))
    store = SQLAlchemyExperimentStore(session)
    service = ExperimentService(
        store,
        SQLAlchemyExperimentDependencyResolver(session, settings=settings),
        experiment_cost_limit=settings.experiment_cost_limit,
    )
    if existing is None:
        adaptive = next(
            value for value in pipelines if value.execution_mode is PipelineExecutionMode.ADAPTIVE
        )
        reference = next(value for value in pipelines if value.name.startswith("P4"))
        fixed = tuple(
            value.id
            for value in pipelines
            if value.execution_mode is PipelineExecutionMode.FIXED
        )
        created = service.create(
            ExperimentCreate(
                name=FIXTURE_EXPERIMENT,
                research_question=(
                    "Do deterministic fixed and adaptive fixture pipelines preserve evidence?"
                ),
                corpus_version_id=version.id,
                benchmark_version_id=benchmark.id,
                pipeline_configuration_ids=tuple(value.id for value in pipelines),
                repetitions=1,
                code_commit=FIXTURE_CODE_COMMIT,
                analysis_configuration=ExperimentAnalysisConfiguration(
                    adaptive_pipeline_id=adaptive.id,
                    fixed_reference_pipeline_id=reference.id,
                    best_observed_fixed_pipeline_ids=fixed,
                ),
            )
        )
        service.freeze(created.id)
        existing = session.get(Experiment, created.id)
        assert existing is not None
    if existing.status is ExperimentStatus.FROZEN:
        from backend.app.jobs.handlers import _query_executor

        service.start(existing.id, execute=_query_executor(session))
    elif existing.status in {ExperimentStatus.RUNNING, ExperimentStatus.PAUSED}:
        from backend.app.jobs.handlers import _query_executor

        service.resume(existing.id, execute=_query_executor(session))
    refreshed = session.get(Experiment, existing.id)
    assert refreshed is not None
    return refreshed


def _export(
    session: Session,
    settings: Settings,
    artifact_store: LocalArtifactStore,
    experiment: Experiment,
) -> Job:
    job, _created = create_job(
        session,
        job_type="experiment_export",
        input_reference={"experiment_id": str(experiment.id), "schema_version": "v1"},
        idempotency_key=(
            f"fixture-export:{experiment.id}:{experiment.configuration_hash}:"
            f"{experiment.completed_at}"
        ),
        progress_total=21,
    )
    session.commit()
    if job.status is JobStatus.QUEUED:
        start_job(job)
        artifact_ids = execute_job(
            session, job, settings=settings, artifact_store=artifact_store
        )
        complete_job(job, result_artifact_ids=artifact_ids)
        session.commit()
    return job


def main() -> None:
    run_fixture()


if __name__ == "__main__":
    main()
