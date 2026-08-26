from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.adaptive.router import DeterministicAdaptiveRouter
from backend.app.adaptive.schemas import (
    CorpusCapabilities,
    QueryClassification,
    RouterInput,
)
from backend.app.adaptive.schemas import (
    RouterConfiguration as AdaptiveRouterConfiguration,
)
from backend.app.artifacts.service import LocalArtifactStore
from backend.app.context.service import (
    ContextBuilder,
    ContextConfiguration,
    load_context_candidates,
    persist_context,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DomainError
from backend.app.db.models import (
    Artifact,
    BenchmarkQuestion,
    BenchmarkVersionStatus,
    CorpusVersion,
    CorpusVersionStatus,
    IndexStatus,
    IndexType,
    PipelineConfiguration,
    PipelineExecutionMode,
    QueryRun,
    QueryRunStatus,
    RetrievalMode,
    RouterConfiguration,
    SearchIndex,
)
from backend.app.documents.chunkers.base import Tokenizer
from backend.app.generation.errors import GenerationProviderError, GenerationTimeoutError
from backend.app.generation.service import GroundedGenerationService
from backend.app.indexing.errors import EmbeddingProviderFailure, IndexNotReady
from backend.app.indexing.service import IndexingService
from backend.app.pipelines.schemas import QueryProcessingConfiguration
from backend.app.providers.registry import (
    UnsupportedProviderError,
    create_embedding_provider,
    create_generation_provider,
    create_reranker_provider,
)
from backend.app.query_processing.service import QueryProcessor
from backend.app.reranking.service import (
    RerankerConfiguration,
    RerankingError,
    RerankingService,
    persist_reranking_results,
)
from backend.app.retrieval.contracts import (
    MetadataFilters,
    RetrievalRequest,
    RetrieverConfiguration,
    RRFConfiguration,
)
from backend.app.retrieval.persistence import persist_retrieval_results
from backend.app.retrieval.service import RetrievalService
from backend.app.tracing.redaction import configured_sensitive_values, redact
from backend.app.tracing.service import SpanHandle, SpanRecorder, TraceRecordingError

from .schemas import QueryRunCreate


@dataclass(frozen=True, slots=True)
class RuntimePipeline:
    retrieval_mode: RetrievalMode
    lexical: dict[str, Any]
    dense: dict[str, Any]
    fusion: dict[str, Any]
    reranker: dict[str, Any]
    context: dict[str, Any]
    route_decision: dict[str, Any]


class QueryOrchestrator:
    """Execute one fixed pipeline while keeping stage services independently replaceable."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.artifact_store = artifact_store or LocalArtifactStore(self.settings.artifact_root)

    def execute(self, payload: QueryRunCreate, *, raise_on_failure: bool = True) -> QueryRun:
        version, pipeline = self._validate(payload)
        processor = QueryProcessor(
            QueryProcessingConfiguration.model_validate(pipeline.query_processing_configuration)
        )
        normalized_query = processor.normalize(payload.query_text)
        configured_mode = RetrievalMode(pipeline.retrieval_mode)
        execution_mode = PipelineExecutionMode(pipeline.execution_mode)
        adaptive = execution_mode is PipelineExecutionMode.ADAPTIVE
        started = perf_counter()
        now = datetime.now(UTC)
        run = QueryRun(
            corpus_version_id=version.id,
            benchmark_question_id=payload.benchmark_question_id,
            pipeline_configuration_id=pipeline.id,
            query_text=payload.query_text,
            normalized_query=normalized_query,
            rewritten_query=None,
            status=QueryRunStatus.RUNNING,
            route_decision=(
                {"type": "adaptive", "status": "pending_classification"}
                if adaptive
                else {
                    "type": "fixed_pipeline",
                    "retrieval_mode": configured_mode.value,
                    "reason": "pipeline_configuration",
                }
            ),
            classification={},
            extracted_metadata={},
            started_at=now,
        )
        self.session.add(run)
        self.session.flush()
        # The run identity is durable before provider/stage work begins, allowing a
        # later failed transaction to roll back and still finalize this run safely.
        self.session.commit()
        self.session.refresh(run)

        recorder = SpanRecorder(self.session, run.id)
        open_spans: list[SpanHandle] = []
        stage = "query_processing"
        try:
            processing_span = self._start_span(
                recorder,
                open_spans,
                "query_processing",
                "Normalize, classify, and optionally rewrite query",
                input_summary={
                    "data_kind": "user_input",
                    "query_character_count": len(payload.query_text),
                    "normalized_character_count": len(normalized_query),
                },
                configuration_snapshot=dict(pipeline.query_processing_configuration),
            )
            metadata = processor.extract_metadata(normalized_query)
            classification_span = self._start_span(
                recorder,
                open_spans,
                "classification",
                "Initial query classification",
                parent=processing_span,
                input_summary={
                    "data_kind": "user_input",
                    "normalized_query": normalized_query,
                    "extracted_metadata": metadata,
                },
                configuration_snapshot={
                    "enabled": processor.configuration.classification_enabled,
                },
            )
            classification = processor.classify(normalized_query, metadata)
            self._succeed_span(
                open_spans,
                classification_span,
                {"data_kind": "application_metadata", **classification},
            )
            runtime = self._runtime_pipeline(
                version=version,
                pipeline=pipeline,
                payload=payload,
                normalized_query=normalized_query,
                classification=classification,
                metadata=metadata,
            )
            retrieval_mode = runtime.retrieval_mode
            run.route_decision = runtime.route_decision
            rewriting_span = self._start_span(
                recorder,
                open_spans,
                "rewriting",
                "Optional deterministic query rewrite",
                parent=processing_span,
                input_summary={"data_kind": "user_input", "query": normalized_query},
                configuration_snapshot={
                    "enabled": (
                        runtime.route_decision.get("rewriting_enabled", False)
                        if adaptive
                        else processor.configuration.rewriting_enabled
                    ),
                    "strategy": processor.configuration.rewriting_strategy,
                },
            )
            if adaptive:
                rewrite_configuration = processor.configuration.model_copy(
                    update={
                        "rewriting_enabled": bool(
                            runtime.route_decision.get("rewriting_enabled", False)
                        )
                    }
                )
                rewritten_query = QueryProcessor(rewrite_configuration).rewrite(normalized_query)
            else:
                rewritten_query = processor.rewrite(normalized_query)
            self._succeed_span(
                open_spans,
                rewriting_span,
                {
                    "data_kind": "application_metadata",
                    "rewritten_query": rewritten_query,
                    "bypassed": rewritten_query is None,
                },
            )
            run.classification = classification
            run.extracted_metadata = metadata
            run.rewritten_query = rewritten_query
            self._succeed_span(
                open_spans,
                processing_span,
                {
                    "data_kind": "application_metadata",
                    "classification_category": classification.get("category"),
                    "rewritten": rewritten_query is not None,
                },
            )
            self._checkpoint(run)

            stage = "routing"
            routing_span = self._start_span(
                recorder,
                open_spans,
                "routing",
                "Apply adaptive route" if adaptive else "Apply configured fixed route",
                input_summary={"data_kind": "application_metadata"},
                configuration_snapshot={
                    "route_type": "adaptive" if adaptive else "fixed_pipeline",
                    "retrieval_mode": retrieval_mode.value,
                    "reranking_enabled": bool(runtime.reranker.get("enabled", False)),
                    "router_configuration_id": (
                        str(pipeline.router_configuration_id) if adaptive else None
                    ),
                },
            )
            self._succeed_span(open_spans, routing_span, dict(run.route_decision))
            self._checkpoint(run)

            query = rewritten_query or normalized_query
            stage = "retrieval"
            retrieval_span = self._start_span(
                recorder,
                open_spans,
                "retrieval",
                "Execute configured retrieval",
                input_summary={
                    "data_kind": "user_input",
                    "query": query,
                    "filter_document_count": len(payload.filters.document_ids),
                    "filter_publication_year_count": len(payload.filters.publication_years),
                },
                configuration_snapshot={
                    "mode": retrieval_mode.value,
                    "lexical": runtime.lexical,
                    "dense": runtime.dense,
                    "fusion": runtime.fusion,
                },
            )
            retrieval_children: dict[str, SpanHandle] = {}

            def observe_retrieval(stage: str, event: str, details: Mapping[str, Any]) -> None:
                if event == "started":
                    retrieval_children[stage] = self._start_span(
                        recorder,
                        open_spans,
                        stage,
                        f"{stage.title()} retrieval stage",
                        parent=retrieval_span,
                        input_summary={
                            "data_kind": "user_input",
                            "query": query,
                            **details,
                        },
                        configuration_snapshot=self._retrieval_stage_configuration(runtime, stage),
                    )
                    return
                child = retrieval_children[stage]
                if event == "succeeded":
                    self._succeed_span(
                        open_spans,
                        child,
                        {"data_kind": "application_metadata", **details},
                    )
                else:
                    self._fail_span(
                        open_spans,
                        child,
                        ("EMBEDDING_PROVIDER_FAILURE" if stage == "dense" else "RETRIEVAL_FAILED"),
                        dict(details),
                    )

            embedding = create_embedding_provider(version.embedding_configuration)
            retrieval = RetrievalService(
                self.session, IndexingService(self.session, embedding)
            ).retrieve(
                self._retrieval_request(payload, runtime, query),
                observer=observe_retrieval,
            )
            persist_retrieval_results(self.session, run.id, retrieval)
            run.route_decision = {
                **run.route_decision,
                "retrieval_disabled": retrieval.retrieval_disabled,
                "candidate_count": len(retrieval.candidates),
                "retrieval_latency_ms": retrieval.total_timing_ms,
            }
            self._succeed_span(
                open_spans,
                retrieval_span,
                {
                    "data_kind": "application_metadata",
                    "retrieval_disabled": retrieval.retrieval_disabled,
                    "candidate_count": len(retrieval.candidates),
                    "latency_ms": retrieval.total_timing_ms,
                },
            )
            self._checkpoint(run)

            stage = "reranking"
            reranker_snapshot = runtime.reranker
            reranker_config = RerankerConfiguration.from_snapshot(reranker_snapshot)
            reranker = (
                create_reranker_provider(reranker_snapshot) if reranker_config.enabled else None
            )
            reranking_span = self._start_span(
                recorder,
                open_spans,
                "reranking",
                "Optionally rerank retrieved candidates",
                input_summary={
                    "data_kind": "retrieved_document_content",
                    "candidate_count": len(retrieval.candidates),
                },
                configuration_snapshot=dict(reranker_snapshot),
            )
            reranking = RerankingService(reranker).execute(
                query=query,
                candidates=retrieval.candidates,
                configuration=reranker_config,
            )
            persist_reranking_results(self.session, query_run_id=run.id, execution=reranking)
            run.route_decision = {
                **run.route_decision,
                "reranking_enabled": reranking.enabled,
                "reranking_latency_ms": reranking.latency_ms,
            }
            self._succeed_span(
                open_spans,
                reranking_span,
                {
                    "data_kind": "application_metadata",
                    "enabled": reranking.enabled,
                    "input_candidate_count": len(retrieval.candidates),
                    "output_candidate_count": len(reranking.candidates),
                    "latency_ms": reranking.latency_ms,
                },
            )
            self._checkpoint(run)

            stage = "context"
            context_config = ContextConfiguration.from_snapshot(runtime.context)
            context_span = self._start_span(
                recorder,
                open_spans,
                "context_construction",
                "Select and serialize generator context",
                input_summary={
                    "data_kind": "retrieved_document_content",
                    "candidate_count": len(reranking.candidates),
                },
                configuration_snapshot=dict(runtime.context),
            )
            context = ContextBuilder().build(
                load_context_candidates(self.session, reranking.candidates),
                context_config,
            )
            safe_context = redact(
                context.context_text,
                sensitive_values=tuple(configured_sensitive_values()),
            )
            if isinstance(safe_context, str) and safe_context != context.context_text:
                context = replace(
                    context,
                    context_text=safe_context,
                    token_count=Tokenizer().count(safe_context),
                )
            context_artifact = persist_context(
                self.session,
                self.artifact_store,
                query_run=run,
                result=context,
                configuration=context_config,
            )
            self._succeed_span(
                open_spans,
                context_span,
                {
                    "data_kind": "application_metadata",
                    "selected_count": len(context.selected),
                    "excluded_count": len(context.excluded),
                    "token_count": context.token_count,
                    "token_budget": context.token_budget,
                },
                artifact_ids=[context_artifact.id],
            )
            context_artifact.trace_span_id = context_span.id
            self._checkpoint(run)

            stage = "generation"
            generation_snapshot = dict(pipeline.generation_configuration)
            generation_provider = create_generation_provider(
                generation_snapshot, settings=self.settings
            )
            generation_span = self._start_span(
                recorder,
                open_spans,
                "generation",
                "Generate grounded structured answer",
                input_summary={
                    "system_instruction": {
                        "data_kind": "system_prompt_instruction",
                        "prompt_reference": pipeline.prompt_versions.get(
                            "grounded_generation"
                        ),
                    },
                    "question": {
                        "data_kind": "user_input",
                        "character_count": len(run.query_text),
                    },
                    "evidence": {
                        "data_kind": "retrieved_document_content_untrusted",
                        "context_artifact_id": str(context_artifact.id),
                        "selected_source_count": len(context.selected),
                    },
                },
                configuration_snapshot=generation_snapshot,
            )
            generation = GroundedGenerationService(
                self.session, self.artifact_store, generation_provider
            ).generate(
                run.id,
                temperature=float(generation_snapshot.get("temperature", 0.0)),
                max_output_tokens=int(generation_snapshot.get("max_output_tokens", 512)),
            )
            self._succeed_span(
                open_spans,
                generation_span,
                {
                    "data_kind": "model_output",
                    "answerability": generation.answer.answerability.value,
                    "claim_count": len(generation.answer.claims),
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "provider": run.generation_metadata.get("provider"),
                    "model": run.generation_metadata.get("model"),
                },
                artifact_ids=[generation.raw_response_artifact_id],
            )
            raw_artifact = self.session.get(Artifact, generation.raw_response_artifact_id)
            if raw_artifact is not None:
                raw_artifact.trace_span_id = generation_span.id

            stage = "claim_processing"
            claim_span = self._start_span(
                recorder,
                open_spans,
                "claim_processing",
                "Persist generated claims",
                input_summary={
                    "data_kind": "model_output",
                    "claim_count": len(generation.answer.claims),
                },
                configuration_snapshot={"schema_version": "grounded-answer-v1"},
            )
            parsed_artifact = self._persist_parsed_response(
                run, generation.answer.model_dump(mode="json")
            )
            citation_span = self._start_span(
                recorder,
                open_spans,
                "citation_validation",
                "Validate citation identifiers and resolve sources",
                parent=claim_span,
                input_summary={
                    "data_kind": "model_output",
                    "citation_count": sum(
                        len(claim.citations) for claim in generation.answer.claims
                    ),
                },
                configuration_snapshot=dict(pipeline.citation_configuration),
            )
            self._succeed_span(
                open_spans,
                citation_span,
                {
                    "data_kind": "application_metadata",
                    "all_citations_resolved": True,
                },
            )
            self._succeed_span(
                open_spans,
                claim_span,
                {
                    "data_kind": "application_metadata",
                    "persisted_claim_count": len(generation.claim_ids),
                },
                artifact_ids=[parsed_artifact.id],
            )
            parsed_artifact.trace_span_id = claim_span.id
            run.total_latency_ms = max(0, round((perf_counter() - started) * 1000))
            self._apply_configured_cost(run, generation_snapshot)
            self.session.commit()
            self.session.refresh(run)
            return run
        except Exception as exc:
            failure_code = self._failure_code(run, stage, exc)
            try:
                for handle in reversed(open_spans.copy()):
                    self._fail_span(
                        open_spans,
                        handle,
                        failure_code,
                        {"exception_type": type(exc).__name__},
                    )
            except TraceRecordingError:
                run_id = run.id
                self.session.rollback()
                persisted_run = self.session.get(QueryRun, run_id)
                if persisted_run is None:
                    raise DomainError(
                        "DATABASE_FAILURE",
                        "The database failed before the query run could be finalized.",
                        status_code=503,
                    ) from exc
                run = persisted_run
                failure_code = "DATABASE_FAILURE"
            if not self.session.is_active:
                run_id = run.id
                self.session.rollback()
                persisted_run = self.session.get(QueryRun, run_id)
                if persisted_run is None:
                    raise DomainError(
                        "DATABASE_FAILURE",
                        "The database failed before the query run could be finalized.",
                        status_code=503,
                    ) from exc
                run = persisted_run
            self._fail(run, stage, exc, started, code=failure_code)
            try:
                self.session.commit()
            except SQLAlchemyError as database_exc:
                self.session.rollback()
                raise DomainError(
                    "DATABASE_FAILURE",
                    "The database could not persist query-run failure state.",
                    status_code=503,
                ) from database_exc
            if not raise_on_failure:
                self.session.refresh(run)
                return run
            raise DomainError(
                run.failure_code or "DATABASE_FAILURE",
                run.failure_message or "The query run failed.",
                status_code=(
                    422
                    if run.failure_code in {"INVALID_CITATION", "INVALID_STRUCTURED_OUTPUT"}
                    else 503
                ),
            ) from exc

    def _validate(self, payload: QueryRunCreate) -> tuple[CorpusVersion, PipelineConfiguration]:
        version = self.session.get(CorpusVersion, payload.corpus_version_id)
        if version is None or version.status != CorpusVersionStatus.READY:
            raise DomainError(
                "CORPUS_VERSION_NOT_READY",
                "Only an existing ready corpus version can be queried.",
                status_code=409,
            )
        pipeline = self.session.get(PipelineConfiguration, payload.pipeline_configuration_id)
        if pipeline is None:
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "The requested pipeline configuration does not exist.",
                status_code=404,
            )
        if not pipeline.is_frozen:
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Query execution requires a frozen pipeline configuration.",
            )
        if payload.benchmark_question_id is not None:
            question = self.session.get(BenchmarkQuestion, payload.benchmark_question_id)
            if question is None:
                raise DomainError(
                    "BENCHMARK_QUESTION_NOT_FOUND",
                    "The linked benchmark question does not exist.",
                    status_code=404,
                )
            if (
                question.benchmark_version.corpus_version_id != version.id
                or question.benchmark_version.status is not BenchmarkVersionStatus.FROZEN
            ):
                raise DomainError(
                    "BENCHMARK_VERSION_UNAVAILABLE",
                    "Query runs may only link a frozen benchmark question for the same corpus.",
                    status_code=409,
                )
        return version, pipeline

    def _runtime_pipeline(
        self,
        *,
        version: CorpusVersion,
        pipeline: PipelineConfiguration,
        payload: QueryRunCreate,
        normalized_query: str,
        classification: dict[str, object],
        metadata: dict[str, object],
    ) -> RuntimePipeline:
        lexical = dict(pipeline.lexical_configuration)
        dense = dict(pipeline.dense_configuration)
        fusion = dict(pipeline.fusion_configuration)
        reranker = dict(pipeline.reranker_configuration)
        context = dict(pipeline.context_configuration)
        if PipelineExecutionMode(pipeline.execution_mode) is PipelineExecutionMode.FIXED:
            mode = RetrievalMode(pipeline.retrieval_mode)
            return RuntimePipeline(
                mode,
                lexical,
                dense,
                fusion,
                reranker,
                context,
                {
                    "type": "fixed_pipeline",
                    "retrieval_mode": mode.value,
                    "reason": "pipeline_configuration",
                },
            )

        router_record = (
            self.session.get(RouterConfiguration, pipeline.router_configuration_id)
            if pipeline.router_configuration_id is not None
            else None
        )
        if router_record is None or not router_record.is_frozen:
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Adaptive execution requires an existing frozen router configuration.",
            )
        router_configuration = AdaptiveRouterConfiguration.model_validate(
            router_record.configuration
        )
        adaptive_limits = dict(pipeline.adaptive_configuration)
        allowed_modes = tuple(
            RetrievalMode(value)
            for value in adaptive_limits.get(
                "allowed_retrieval_modes",
                [mode.value for mode in router_configuration.allowed_retrieval_modes],
            )
            if RetrievalMode(value) in router_configuration.allowed_retrieval_modes
        )
        router_configuration = router_configuration.model_copy(
            update={"allowed_retrieval_modes": allowed_modes}
        )
        available_index_types = set(
            self.session.scalars(
                select(SearchIndex.index_type).where(
                    SearchIndex.corpus_version_id == version.id,
                    SearchIndex.status == IndexStatus.READY,
                )
            )
        )
        capabilities = CorpusCapabilities(
            lexical_available=IndexType.LEXICAL in available_index_types,
            dense_available=IndexType.DENSE in available_index_types,
            reranker_available=bool(adaptive_limits.get("allow_reranking", True)),
            rewriting_available=bool(adaptive_limits.get("allow_rewriting", True)),
            maximum_candidate_count=int(
                adaptive_limits.get("maximum_candidate_count", 200)
            ),
            maximum_context_budget=int(
                adaptive_limits.get("maximum_context_budget", 100_000)
            ),
        )
        metadata_filters: dict[str, object] = {}
        if payload.filters.document_ids:
            metadata_filters["document_ids"] = [
                str(value) for value in payload.filters.document_ids
            ]
        if payload.filters.publication_years:
            metadata_filters["publication_years"] = list(payload.filters.publication_years)
        entities = metadata.get("entities")
        entity_count = len(entities) if isinstance(entities, list) else 0
        decision = DeterministicAdaptiveRouter(router_configuration).route(
            RouterInput(
                query=normalized_query,
                classification=QueryClassification.model_validate(classification),
                metadata_filters=metadata_filters,
                quoted_phrase_count=len(re.findall(r'["“][^"”]+["”]', normalized_query)),
                identifier_count=entity_count,
                capabilities=capabilities,
            )
        )
        candidate_count = decision.candidate_count
        lexical["candidate_count"] = max(1, candidate_count)
        dense["candidate_count"] = max(1, candidate_count)
        if candidate_count:
            lexical["top_k"] = min(int(lexical.get("top_k", 10)), candidate_count)
            dense["top_k"] = min(int(dense.get("top_k", 10)), candidate_count)
            fusion["final_count"] = min(
                int(fusion.get("final_count", 10)), candidate_count
            )
        reranker.update(
            {
                "enabled": decision.reranking_enabled,
                "input_candidate_count": max(1, candidate_count),
                "final_count": min(
                    int(reranker.get("final_count", 10)), max(1, candidate_count)
                ),
            }
        )
        context["token_budget"] = decision.context_budget
        route_decision = {
            "type": "adaptive",
            **decision.model_dump(mode="json"),
            "router_configuration_id": str(router_record.id),
            "router_configuration_hash": router_record.configuration_hash,
            "capabilities": capabilities.model_dump(mode="json"),
        }
        return RuntimePipeline(
            decision.retrieval_mode,
            lexical,
            dense,
            fusion,
            reranker,
            context,
            route_decision,
        )

    @staticmethod
    def _retrieval_request(
        payload: QueryRunCreate, pipeline: RuntimePipeline, query: str
    ) -> RetrievalRequest:
        lexical = pipeline.lexical
        dense = pipeline.dense
        fusion = pipeline.fusion
        mode = pipeline.retrieval_mode
        if mode == RetrievalMode.LEXICAL:
            top_k = int(lexical.get("top_k", 10))
        elif mode == RetrievalMode.DENSE:
            top_k = int(dense.get("top_k", 10))
        else:
            top_k = int(fusion.get("final_count", 10))
        return RetrievalRequest(
            corpus_version_id=payload.corpus_version_id,
            query=query,
            mode=mode,
            top_k=top_k,
            filters=MetadataFilters(
                document_ids=frozenset(payload.filters.document_ids),
                publication_years=frozenset(payload.filters.publication_years),
            ),
            lexical=RetrieverConfiguration(candidate_count=int(lexical.get("candidate_count", 20))),
            dense=RetrieverConfiguration(candidate_count=int(dense.get("candidate_count", 20))),
            fusion=RRFConfiguration(
                rank_constant=int(fusion.get("rrf_k", 60)),
                lexical_weight=float(fusion.get("lexical_weight", 1.0)),
                dense_weight=float(fusion.get("dense_weight", 1.0)),
            ),
        )

    @staticmethod
    def _apply_configured_cost(run: QueryRun, configuration: dict[str, Any]) -> None:
        if run.estimated_cost is not None:
            return
        input_price = configuration.get("input_price_per_million_tokens")
        output_price = configuration.get("output_price_per_million_tokens")
        if (
            run.input_tokens is not None
            and run.output_tokens is not None
            and input_price is not None
            and output_price is not None
        ):
            run.estimated_cost = (
                run.input_tokens * float(input_price) + run.output_tokens * float(output_price)
            ) / 1_000_000

    def _checkpoint(self, run: QueryRun) -> None:
        """Make completed stages durable so later failures retain a partial trace."""

        self.session.commit()
        self.session.refresh(run)

    @staticmethod
    def _start_span(
        recorder: SpanRecorder,
        open_spans: list[SpanHandle],
        span_type: str,
        name: str,
        **metadata: Any,
    ) -> SpanHandle:
        handle = recorder.start(span_type, name, **metadata)
        open_spans.append(handle)
        return handle

    @staticmethod
    def _succeed_span(
        open_spans: list[SpanHandle],
        handle: SpanHandle,
        output_summary: dict[str, Any],
        *,
        artifact_ids: list[Any] | tuple[Any, ...] = (),
    ) -> None:
        handle.succeed(output_summary, artifact_ids=artifact_ids)
        open_spans.remove(handle)

    @staticmethod
    def _fail_span(
        open_spans: list[SpanHandle],
        handle: SpanHandle,
        error_code: str,
        output_summary: dict[str, Any],
    ) -> None:
        handle.fail(error_code, output_summary)
        open_spans.remove(handle)

    @staticmethod
    def _retrieval_stage_configuration(
        pipeline: RuntimePipeline, stage: str
    ) -> dict[str, Any]:
        if stage == "lexical":
            return dict(pipeline.lexical)
        if stage == "dense":
            return dict(pipeline.dense)
        return dict(pipeline.fusion)

    def _persist_parsed_response(self, run: QueryRun, parsed_response: dict[str, Any]) -> Artifact:
        safe_response = redact(
            parsed_response,
            sensitive_values=tuple(configured_sensitive_values()),
        )
        payload = json.dumps(
            safe_response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = self.artifact_store.put_bytes(
            payload,
            media_type="application/json",
            original_filename=f"query-{run.id}-parsed-response.json",
            producing_operation="grounded_generation_parsed_response",
            configuration={"schema_version": "grounded-answer-v1"},
        )
        artifact = Artifact(
            id=descriptor.id,
            corpus_version_id=run.corpus_version_id,
            document_id=None,
            job_id=None,
            query_run_id=run.id,
            trace_span_id=None,
            artifact_type="parsed_model_response",
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=descriptor.original_filename,
            producing_operation=descriptor.producing_operation,
            producer_version="grounded-answer-v1",
            configuration=descriptor.configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    @staticmethod
    def _failure_code(run: QueryRun, stage: str, exc: Exception) -> str:
        existing = run.failure_code
        if existing in {"INVALID_CITATION", "INVALID_STRUCTURED_OUTPUT"}:
            return existing
        if isinstance(exc, DomainError):
            return exc.code
        if isinstance(exc, IndexNotReady):
            return "RETRIEVAL_FAILED"
        if isinstance(exc, EmbeddingProviderFailure):
            return "EMBEDDING_PROVIDER_FAILURE"
        if isinstance(exc, RerankingError):
            return "RERANK_FAILED"
        if isinstance(exc, GenerationTimeoutError):
            return "TIMEOUT"
        if isinstance(exc, GenerationProviderError):
            return "MODEL_PROVIDER_FAILURE"
        if stage == "generation" and isinstance(exc, ValueError):
            return "MODEL_PROVIDER_FAILURE"
        if isinstance(exc, UnsupportedProviderError):
            return "ROUTE_UNAVAILABLE"
        if isinstance(exc, (SQLAlchemyError, TraceRecordingError)):
            return "DATABASE_FAILURE"
        return {
            "retrieval": "RETRIEVAL_FAILED",
            "reranking": "RERANK_FAILED",
            "context": "CONTEXT_BUILD_FAILED",
            "generation": "GENERATION_FAILED",
            "claim_processing": "INVALID_STRUCTURED_OUTPUT",
        }.get(stage, "DATABASE_FAILURE")

    @staticmethod
    def _fail(
        run: QueryRun,
        stage: str,
        exc: Exception,
        started: float,
        *,
        code: str,
    ) -> None:
        run.status = QueryRunStatus.FAILED
        run.failure_code = code
        run.failure_message = f"{stage.replace('_', ' ').title()} failed ({type(exc).__name__})."
        run.finished_at = datetime.now(UTC)
        run.total_latency_ms = max(0, round((perf_counter() - started) * 1000))
