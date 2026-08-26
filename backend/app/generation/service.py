from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.citations.errors import CitationValidationError
from backend.app.citations.service import CitationPersistenceService
from backend.app.db.models import (
    Artifact,
    QueryRun,
    QueryRunStatus,
)
from backend.app.prompts.service import PromptRegistry
from backend.app.providers.base import GenerationProvider, StructuredGenerationResult
from backend.app.tracing.redaction import configured_sensitive_values, redact

from .errors import (
    InvalidStructuredOutputError,
    QueryRunNotFoundError,
    QueryRunStateError,
)
from .schemas import GroundedAnswer


@dataclass(frozen=True, slots=True)
class GenerationExecution:
    answer: GroundedAnswer
    raw_response_artifact_id: UUID
    claim_ids: tuple[UUID, ...]


class GroundedGenerationService:
    """Render, generate, validate, resolve citations, and persist audit records."""

    def __init__(
        self,
        session: Session,
        artifact_store: LocalArtifactStore,
        provider: GenerationProvider,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.provider = provider

    def generate(
        self,
        query_run_id: UUID,
        *,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
    ) -> GenerationExecution:
        query_run = self.session.get(QueryRun, query_run_id)
        if query_run is None:
            raise QueryRunNotFoundError(f"query run does not exist: {query_run_id}")
        if query_run.status == QueryRunStatus.SUCCEEDED:
            raise QueryRunStateError("a succeeded query run cannot be regenerated in place")

        query_run.status = QueryRunStatus.RUNNING
        query_run.started_at = query_run.started_at or datetime.now(UTC)
        query_run.finished_at = None
        query_run.failure_code = None
        query_run.failure_message = None
        citation_service = CitationPersistenceService(self.session)
        sources = citation_service.context_sources(query_run)
        context_text = self._persisted_context(query_run)
        rendered = PromptRegistry(self.session).render_grounded(
            question=query_run.query_text,
            evidence=[source.prompt_record() for source in sources],
            context_text=context_text,
        )
        query_run.prompt_template_id = UUID(rendered.prompt_template_id)
        self.session.flush()

        try:
            response = self.provider.generate_structured(
                rendered.user_prompt,
                system_prompt=rendered.system_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            self._record_failure(query_run, getattr(exc, "code", "MODEL_PROVIDER_FAILURE"), exc)
            raise

        artifact = self._persist_raw_response(query_run, response, rendered.content_hash)
        query_run.raw_response_artifact_id = artifact.id
        self._record_usage(
            query_run,
            response,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
            prompt_hash=rendered.content_hash,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            answer = GroundedAnswer.model_validate_json(response.content)
            citation_service.validate_ids(answer, sources)
            claims = citation_service.replace_claims_and_citations(query_run, answer, sources)
        except ValidationError as exc:
            wrapped = InvalidStructuredOutputError(str(exc))
            self._record_failure(query_run, wrapped.code, wrapped)
            self.session.flush()
            raise wrapped from exc
        except CitationValidationError as exc:
            self._record_failure(query_run, exc.code, exc)
            self.session.flush()
            raise

        query_run.answer_text = answer.answer
        query_run.answerability_decision = answer.answerability
        query_run.limitations = list(answer.limitations)
        query_run.abstention_reason = answer.abstention_reason
        query_run.status = QueryRunStatus.SUCCEEDED
        query_run.finished_at = datetime.now(UTC)
        self.session.flush()
        return GenerationExecution(
            answer=answer,
            raw_response_artifact_id=artifact.id,
            claim_ids=tuple(claim.id for claim in claims),
        )

    def _persisted_context(self, query_run: QueryRun) -> str | None:
        if query_run.context_artifact_id is None:
            return None
        artifact = self.session.get(Artifact, query_run.context_artifact_id)
        if artifact is None or artifact.artifact_type != "generator-context":
            raise QueryRunStateError("the persisted generator context is unavailable")
        return self.artifact_store.read_bytes(artifact.storage_key).decode("utf-8")

    def _persist_raw_response(
        self,
        query_run: QueryRun,
        response: StructuredGenerationResult,
        prompt_hash: str,
    ) -> Artifact:
        configuration: dict[str, object] = {
            "provider": response.provider_id,
            "model": response.model_id,
            "prompt_hash": prompt_hash,
        }
        safe_raw = redact(
            response.raw_response,
            sensitive_values=tuple(configured_sensitive_values()),
        )
        if not isinstance(safe_raw, str):  # pragma: no cover - type safety boundary
            raise TypeError("Raw generation response must remain text after redaction")
        descriptor = self.artifact_store.put_bytes(
            safe_raw.encode(),
            media_type="application/json",
            producing_operation="grounded_generation_raw_response",
            configuration=configuration,
        )
        artifact = Artifact(
            id=descriptor.id,
            corpus_version_id=query_run.corpus_version_id,
            document_id=None,
            job_id=None,
            query_run_id=query_run.id,
            trace_span_id=None,
            artifact_type="raw_model_response",
            content_hash=descriptor.content_hash,
            media_type=descriptor.media_type,
            original_filename=None,
            producing_operation=descriptor.producing_operation,
            producer_version=prompt_hash,
            configuration=configuration,
            storage_key=descriptor.storage_key,
            size_bytes=descriptor.size_bytes,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    @staticmethod
    def _record_usage(
        query_run: QueryRun,
        response: StructuredGenerationResult,
        *,
        prompt_id: str,
        prompt_version: int,
        prompt_hash: str,
        temperature: float,
        max_output_tokens: int | None,
    ) -> None:
        query_run.input_tokens = response.input_tokens
        query_run.output_tokens = response.output_tokens
        query_run.estimated_cost = response.estimated_cost
        query_run.total_latency_ms = response.latency_ms
        query_run.generation_metadata = {
            "provider": response.provider_id,
            "model": response.model_id,
            "provider_request_id": response.request_id,
            "latency_ms": response.latency_ms,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "provider_metadata": dict(response.metadata),
        }

    @staticmethod
    def _record_failure(query_run: QueryRun, code: str, exc: Exception) -> None:
        query_run.status = QueryRunStatus.FAILED
        query_run.failure_code = code
        # Validation diagnostics are useful, but never include the raw response or
        # provider credentials. The exact raw payload is retained as an artifact.
        query_run.failure_message = str(exc)[:2_000]
        query_run.finished_at = datetime.now(UTC)
