from __future__ import annotations

import pytest
from backend.app.core.errors import DomainError
from backend.app.db.models import RetrievalMode
from backend.app.pipelines.schemas import PipelineConfigurationCreate
from backend.app.pipelines.service import (
    create_pipeline_configuration,
    freeze_pipeline_configuration,
)
from sqlalchemy.orm import Session


def test_pipeline_snapshot_hash_and_freeze_are_deterministic(session: Session) -> None:
    payload = PipelineConfigurationCreate(name="baseline", retrieval_mode=RetrievalMode.HYBRID)
    first = create_pipeline_configuration(session, payload)
    second = create_pipeline_configuration(
        session,
        payload.model_copy(update={"name": "baseline-copy"}),
    )
    assert first.configuration_hash == second.configuration_hash

    freeze_pipeline_configuration(session, first)
    session.flush()
    first.generation_configuration = {"provider": "fake", "model": "changed"}
    with pytest.raises(DomainError) as raised:
        session.flush()
    assert raised.value.code == "PIPELINE_CONFIGURATION_IMMUTABLE"


def test_pipeline_rejects_reranking_without_retrieval() -> None:
    with pytest.raises(ValueError, match="reranking"):
        PipelineConfigurationCreate(
            name="invalid",
            retrieval_mode=RetrievalMode.NONE,
            reranker_configuration={"enabled": True},
        )

