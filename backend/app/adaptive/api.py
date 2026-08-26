from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.db.models import RouterConfiguration as RouterConfigurationRecord
from backend.app.db.session import get_db

from .schemas import RouterConfigurationCreate, RouterConfigurationRead
from .service import (
    create_router_configuration,
    freeze_router_configuration,
    get_router_configuration,
    list_router_configurations,
)

router = APIRouter(tags=["adaptive-routing"])


@router.post(
    "/router-configurations",
    response_model=RouterConfigurationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_router(
    payload: RouterConfigurationCreate,
    session: Annotated[Session, Depends(get_db)],
) -> RouterConfigurationRecord:
    record = create_router_configuration(session, payload)
    session.commit()
    session.refresh(record)
    return record


@router.get("/router-configurations", response_model=list[RouterConfigurationRead])
def list_routers(
    session: Annotated[Session, Depends(get_db)],
) -> list[RouterConfigurationRecord]:
    return list_router_configurations(session)


@router.get("/router-configurations/{configuration_id}", response_model=RouterConfigurationRead)
def get_router(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> RouterConfigurationRecord:
    return get_router_configuration(session, configuration_id)


@router.post(
    "/router-configurations/{configuration_id}/freeze",
    response_model=RouterConfigurationRead,
)
def freeze_router(
    configuration_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> RouterConfigurationRecord:
    record = get_router_configuration(session, configuration_id)
    freeze_router_configuration(session, record)
    session.commit()
    session.refresh(record)
    return record

