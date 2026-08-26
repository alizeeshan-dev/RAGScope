from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import (
    PipelineConfiguration,
    PipelineExecutionMode,
    RouterConfiguration,
)
from backend.app.pipelines.schemas import PipelineConfigurationCreate
from backend.app.prompts.service import PromptRegistry


def configuration_payload(payload: PipelineConfigurationCreate) -> dict[str, object]:
    value = payload.model_dump(mode="json")
    value.pop("name", None)
    value.pop("version", None)
    return value


def configuration_hash(payload: PipelineConfigurationCreate) -> str:
    return hashlib.sha256(canonical_json(configuration_payload(payload))).hexdigest()


def create_pipeline_configuration(
    session: Session, payload: PipelineConfigurationCreate
) -> PipelineConfiguration:
    values = payload.model_dump(mode="json")
    values["execution_mode"] = payload.execution_mode
    values["router_configuration_id"] = payload.router_configuration_id
    values["retrieval_mode"] = payload.retrieval_mode
    configuration = PipelineConfiguration(
        **values,
        configuration_hash=configuration_hash(payload),
    )
    session.add(configuration)
    try:
        session.flush()
    except IntegrityError as exc:
        raise DomainError(
            "PIPELINE_VERSION_CONFLICT",
            "A pipeline configuration with this name and version already exists.",
        ) from exc
    return configuration


def get_pipeline_or_error(session: Session, configuration_id: UUID) -> PipelineConfiguration:
    configuration = session.get(PipelineConfiguration, configuration_id)
    if configuration is None:
        raise DomainError(
            "PIPELINE_CONFIGURATION_NOT_FOUND",
            "The requested pipeline configuration does not exist.",
            status_code=404,
        )
    return configuration


def freeze_pipeline_configuration(
    session: Session, configuration: PipelineConfiguration
) -> PipelineConfiguration:
    if configuration.frozen_at is not None:
        raise DomainError(
            "PIPELINE_CONFIGURATION_IMMUTABLE",
            "The pipeline configuration is already frozen.",
        )
    if PipelineExecutionMode(configuration.execution_mode) is PipelineExecutionMode.ADAPTIVE:
        router = (
            session.get(RouterConfiguration, configuration.router_configuration_id)
            if configuration.router_configuration_id is not None
            else None
        )
        if router is None or not router.is_frozen:
            raise DomainError(
                "ROUTE_UNAVAILABLE",
                "Adaptive pipelines require an existing frozen router configuration.",
            )
    prompt_reference = configuration.prompt_versions.get("grounded_generation", {})
    prompt = PromptRegistry(session).ensure_grounded_prompt()
    if (
        prompt_reference.get("prompt_id") != prompt.prompt_id
        or prompt_reference.get("version") != prompt.version
    ):
        raise DomainError(
            "ROUTE_UNAVAILABLE",
            "The pipeline references an unavailable grounded-generation prompt version.",
        )
    configuration.frozen_at = datetime.now(UTC)
    session.flush()
    return configuration


def list_pipeline_configurations(session: Session) -> list[PipelineConfiguration]:
    return list(
        session.scalars(
            select(PipelineConfiguration).order_by(
                PipelineConfiguration.name,
                PipelineConfiguration.version,
            )
        )
    )
