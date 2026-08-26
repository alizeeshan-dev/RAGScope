from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.db.models import PipelineConfiguration
from backend.app.db.session import get_db
from backend.app.pipelines.schemas import (
    PipelineConfigurationCreate,
    PipelineConfigurationRead,
)
from backend.app.pipelines.service import (
    create_pipeline_configuration,
    freeze_pipeline_configuration,
    get_pipeline_or_error,
    list_pipeline_configurations,
)

router = APIRouter(tags=["pipelines"])


@router.post(
    "/pipeline-configurations",
    response_model=PipelineConfigurationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_pipeline(
    payload: PipelineConfigurationCreate,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    configuration = create_pipeline_configuration(session, payload)
    session.commit()
    session.refresh(configuration)
    return configuration


@router.get("/pipeline-configurations", response_model=list[PipelineConfigurationRead])
def list_pipelines(
    session: Annotated[Session, Depends(get_db)],
) -> list[PipelineConfiguration]:
    return list_pipeline_configurations(session)


@router.get(
    "/pipeline-configurations/{configuration_id}",
    response_model=PipelineConfigurationRead,
)
def get_pipeline(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    return get_pipeline_or_error(session, configuration_id)


@router.post(
    "/pipeline-configurations/{configuration_id}/freeze",
    response_model=PipelineConfigurationRead,
)
def freeze_pipeline(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> PipelineConfiguration:
    configuration = get_pipeline_or_error(session, configuration_id)
    freeze_pipeline_configuration(session, configuration)
    session.commit()
    session.refresh(configuration)
    return configuration
