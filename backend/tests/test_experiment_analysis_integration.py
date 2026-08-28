from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.app.analysis.service import ExperimentAnalysisService
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Answerability,
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
    ContextSource,
    Corpus,
    CorpusVersion,
    CorpusVersionStatus,
    EvaluationMetricScope,
    EvaluationResult,
    Experiment,
    ExperimentCellStatus,
    ExperimentRun,
    ExperimentStatus,
    PipelineConfiguration,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RetrievalResultRecord,
    SourceDocument,
)
from backend.app.evaluation.schemas import HumanMetricCreate
from backend.app.evaluation.service import EvaluationService
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _persist_completed_experiment(session: Session) -> tuple[Experiment, QueryRun]:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    corpus = Corpus(name="analysis-corpus")
    session.add(corpus)
    session.flush()
    version = CorpusVersion(
        corpus_id=corpus.id,
        version_label="v1",
        status=CorpusVersionStatus.READY,
        content_hash="a" * 64,
        frozen_at=now,
    )
    session.add(version)
    session.flush()
    document = SourceDocument(
        corpus_version_id=version.id,
        title="Dataset paper",
        file_hash="b" * 64,
        mime_type="application/pdf",
    )
    session.add(document)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        corpus_version_id=version.id,
        chunker_id="fixed-v1",
        sequence_number=1,
        text="The dataset contains 100 examples.",
        token_count=7,
        content_hash="c" * 64,
    )
    session.add(chunk)

    benchmark = Benchmark(name="analysis-benchmark")
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
        question_text="How many examples are in the dataset?",
        question_type=BenchmarkQuestionType.DIRECT_FACT_LOOKUP,
        difficulty=BenchmarkDifficulty.EASY,
        answerable=True,
        expected_answerability=Answerability.ANSWERABLE,
        reference_answer="100 examples",
        annotation_status=BenchmarkAnnotationStatus.REVIEWED,
    )
    session.add(question)
    session.flush()
    evidence_set = BenchmarkEvidenceSet(
        benchmark_question_id=question.id,
        set_number=1,
    )
    session.add(evidence_set)
    session.flush()
    session.add(
        BenchmarkEvidenceReference(
            evidence_set_id=evidence_set.id,
            document_id=document.id,
            chunk_id=chunk.id,
            selected_text=chunk.text,
            evidence_role="required",
        )
    )
    session.flush()
    benchmark_version.status = BenchmarkVersionStatus.FROZEN
    benchmark_version.frozen_at = now
    session.commit()

    pipeline = PipelineConfiguration(
        name="P1 lexical",
        version=1,
        retrieval_mode=RetrievalMode.LEXICAL,
        configuration_hash="d" * 64,
        frozen_at=now,
    )
    session.add(pipeline)
    session.flush()
    experiment = Experiment(
        name="analysis experiment",
        research_question="Does lexical retrieval recover evidence?",
        corpus_version_id=version.id,
        benchmark_version_id=benchmark_version.id,
        pipeline_configuration_ids=[str(pipeline.id)],
        repetitions=1,
        status=ExperimentStatus.RUNNING,
        code_commit="abcdef123456",
        retry_policy={"max_attempts": 2},
        dependency_snapshot={"schema_version": "test"},
        configuration_hash="e" * 64,
        estimated_cost_currency="USD",
        frozen_at=now,
        started_at=now,
    )
    session.add(experiment)
    session.flush()
    cell = ExperimentRun(
        experiment_id=experiment.id,
        benchmark_question_id=question.id,
        pipeline_configuration_id=pipeline.id,
        repetition_index=1,
        query_text=question.question_text,
        status=ExperimentCellStatus.SUCCEEDED,
        idempotency_key="f" * 64,
        random_seed=42,
        attempt_count=1,
        max_attempts=2,
        finished_at=now,
    )
    session.add(cell)
    session.flush()
    run = QueryRun(
        corpus_version_id=version.id,
        benchmark_question_id=question.id,
        pipeline_configuration_id=pipeline.id,
        query_text=question.question_text,
        normalized_query=question.question_text,
        status=QueryRunStatus.SUCCEEDED,
        route_decision={"type": "fixed_pipeline", "retrieval_mode": "lexical"},
        answer_text="100 examples [S1]",
        answerability_decision=Answerability.ANSWERABLE,
        total_latency_ms=120,
        input_tokens=80,
        output_tokens=12,
        estimated_cost=0.0002,
        experiment_run_id=cell.id,
        experiment_attempt_number=1,
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RetrievalResultRecord(
                query_run_id=run.id,
                chunk_id=chunk.id,
                retriever_type="lexical",
                original_rank=1,
                original_score=1.0,
                selected_for_context=True,
            ),
            ContextSource(
                query_run_id=run.id,
                chunk_id=chunk.id,
                document_id=document.id,
                citation_id="S1",
                sequence_number=1,
                selected=True,
                token_count=7,
            ),
            EvaluationResult(
                query_run_id=run.id,
                metric_name="recall_at_k",
                metric_scope=EvaluationMetricScope.RETRIEVAL,
                metric_value=1.0,
                metric_version="retrieval-evidence-v1",
                evaluation_method="deterministic_human_benchmark_evidence",
                details={"api_key": "must-not-export"},
                input_snapshot={},
                input_hash="1" * 64,
            ),
            EvaluationResult(
                query_run_id=run.id,
                metric_name="answer_correctness",
                metric_scope=EvaluationMetricScope.GENERATION,
                metric_value=1.0,
                metric_version="generation-quality-v1",
                evaluation_method="human",
                details={"label_origin": "human_review"},
                input_snapshot={},
                input_hash="2" * 64,
            ),
        ]
    )
    session.commit()
    experiment.status = ExperimentStatus.COMPLETED
    experiment.completed_at = now
    session.commit()
    return experiment, run


def test_results_aggregate_denominators_survival_and_redacted_export(
    session: Session, client: TestClient
) -> None:
    experiment, run = _persist_completed_experiment(session)

    result = ExperimentAnalysisService(session).results(experiment.id)
    assert result.sample_size == 1
    assert result.infrastructure_failures == 0
    assert result.runs[0].evidence_survival is not None
    assert result.runs[0].evidence_survival.context == 1.0
    recall = next(row for row in result.aggregates if row.metric_name == "recall_at_k")
    assert (recall.mean, recall.denominator_count, recall.missing_metric_count) == (1.0, 1, 0)

    response = client.get(f"/api/v1/experiments/{experiment.id}/results")
    generation = client.post(f"/api/v1/experiments/{experiment.id}/exports?wait=true")
    raw_csv = client.get(f"/api/v1/experiments/{experiment.id}/export?format=csv")
    exported_json = client.get(f"/api/v1/experiments/{experiment.id}/export?format=json")
    assert generation.status_code == 200
    assert response.status_code == raw_csv.status_code == exported_json.status_code == 200
    assert response.json()["runs"][0]["run_id"] == str(run.id)
    assert "must-not-export" not in raw_csv.text
    assert "must-not-export" not in exported_json.text
    assert "[REDACTED]" in exported_json.text
    exported = exported_json.json()
    assert exported["research_audit"]["manifest_version"] == (
        "ragscope-research-audit.v1"
    )
    assert exported["research_audit"]["experiment"]["configuration_hash"] == "e" * 64
    assert "storage_key" not in exported_json.text


def test_completed_experiment_raw_query_run_is_immutable(session: Session) -> None:
    _experiment, run = _persist_completed_experiment(session)
    run.answer_text = "rewritten result"
    with pytest.raises(DomainError) as error:
        session.commit()
    assert error.value.code == "EXPERIMENT_RAW_RESULTS_IMMUTABLE"


def test_completed_experiment_accepts_append_only_human_evaluation(session: Session) -> None:
    _experiment, run = _persist_completed_experiment(session)
    metric = EvaluationService(session).add_human_metric(
        run.id,
        HumanMetricCreate(
            metric_name="answer_correctness",
            metric_value=1.0,
            reviewer_label="reviewer",
        ),
    )
    session.commit()
    assert metric.evaluation_method == "human"

    metric.metric_value = 0.0
    with pytest.raises(DomainError) as error:
        session.commit()
    assert error.value.code == "EXPERIMENT_RAW_RESULTS_IMMUTABLE"


def test_analysis_uses_latest_versioned_metric_input_without_deleting_history(
    session: Session,
) -> None:
    experiment, run = _persist_completed_experiment(session)
    service = EvaluationService(session)
    for value in (0.25, 0.75):
        service.add_human_metric(
            run.id,
            HumanMetricCreate(
                metric_name="answer_correctness",
                metric_value=value,
                reviewer_label="reviewer",
            ),
        )
        session.commit()

    stored = [
        row
        for row in service.list_results(run.id)
        if row.metric_name == "answer_correctness"
        and row.metric_version == "human-review.v1"
    ]
    assert len(stored) == 2
    projected = ExperimentAnalysisService(session).all_runs(experiment.id)[0]
    selected = [
        metric
        for metric in projected.metrics
        if metric.name == "answer_correctness"
        and metric.version == "human-review.v1"
    ]
    assert len(selected) == 1
    assert selected[0].value == 0.75
