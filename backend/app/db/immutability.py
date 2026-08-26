from typing import Any, cast

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.db.models import (
    BenchmarkEvidenceReference,
    BenchmarkEvidenceSet,
    BenchmarkQuestion,
    BenchmarkVersion,
    BenchmarkVersionStatus,
    CorpusVersion,
    CorpusVersionStatus,
    PipelineConfiguration,
    PromptTemplate,
    RouterConfiguration,
)


@event.listens_for(Session, "before_flush")
def protect_version_configuration(
    session: Session, _flush_context: object, _instances: object
) -> None:
    """Provide an ORM-level backstop in addition to service/API state checks."""

    configuration_fields = (
        "parser_configuration",
        "chunker_configuration",
        "embedding_configuration",
    )
    for obj in session.dirty:
        if isinstance(obj, (PipelineConfiguration, PromptTemplate, RouterConfiguration)):
            state = cast(Any, inspect(obj))
            frozen_history = state.attrs.frozen_at.history
            freezing_now = obj.frozen_at is not None and frozen_history.has_changes()
            if obj.frozen_at is not None and not freezing_now:
                raise DomainError(
                    (
                        "PIPELINE_CONFIGURATION_IMMUTABLE"
                        if isinstance(obj, PipelineConfiguration)
                        else (
                            "ROUTER_CONFIGURATION_IMMUTABLE"
                            if isinstance(obj, RouterConfiguration)
                            else "PROMPT_IMMUTABLE"
                        )
                    ),
                    "A frozen configuration snapshot cannot be modified.",
                )
        if not isinstance(obj, CorpusVersion):
            continue
        state = inspect(obj)
        frozen_history = state.attrs.frozen_at.history
        freezing_now = obj.frozen_at is not None and frozen_history.has_changes()
        if obj.frozen_at is not None and not freezing_now:
            raise DomainError(
                "CORPUS_VERSION_IMMUTABLE", "A frozen corpus version cannot be modified."
            )
        if obj.status is not CorpusVersionStatus.DRAFT and any(
            state.attrs[field].history.has_changes() for field in configuration_fields
        ):
            raise DomainError(
                "CORPUS_VERSION_IMMUTABLE",
                "Processing configuration can only change while the version is a draft.",
            )

    for obj in {*session.new, *session.dirty, *session.deleted}:
        version = _benchmark_version(obj, session)
        if version is None or version.status is not BenchmarkVersionStatus.FROZEN:
            continue
        if isinstance(obj, BenchmarkVersion) and obj in session.dirty:
            state = cast(Any, inspect(obj))
            freezing_now = (
                state.attrs.status.history.has_changes()
                and obj.status is BenchmarkVersionStatus.FROZEN
            )
            if freezing_now:
                continue
        raise DomainError(
            "BENCHMARK_VERSION_IMMUTABLE",
            "A frozen benchmark version and its annotations cannot be modified.",
            status_code=409,
        )


def _benchmark_version(obj: object, session: Session) -> BenchmarkVersion | None:
    if isinstance(obj, BenchmarkVersion):
        return obj
    if isinstance(obj, BenchmarkQuestion):
        return obj.benchmark_version or session.get(
            BenchmarkVersion, obj.benchmark_version_id
        )
    if isinstance(obj, BenchmarkEvidenceSet):
        question = obj.benchmark_question or session.get(
            BenchmarkQuestion, obj.benchmark_question_id
        )
        return _benchmark_version(question, session) if question is not None else None
    if isinstance(obj, BenchmarkEvidenceReference):
        evidence_set = obj.evidence_set or session.get(
            BenchmarkEvidenceSet, obj.evidence_set_id
        )
        return (
            _benchmark_version(evidence_set, session)
            if evidence_set is not None
            else None
        )
    return None
