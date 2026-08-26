from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.corpora.hashing import canonical_json
from backend.app.db.models import RouterConfiguration as RouterConfigurationRecord

from .schemas import RouterConfiguration, RouterConfigurationCreate


def create_router_configuration(
    session: Session, payload: RouterConfigurationCreate
) -> RouterConfigurationRecord:
    snapshot = payload.configuration.model_dump(mode="json")
    record = RouterConfigurationRecord(
        name=payload.name.strip(),
        version=payload.version,
        router_type="rule_based",
        router_version=payload.configuration.version,
        classifier_version="1.0.0",
        configuration=snapshot,
        configuration_hash=hashlib.sha256(canonical_json(snapshot)).hexdigest(),
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError as exc:
        raise DomainError(
            "ROUTER_VERSION_CONFLICT",
            "A router configuration with this name and version already exists.",
        ) from exc
    return record


def get_router_configuration(
    session: Session, configuration_id: UUID
) -> RouterConfigurationRecord:
    record = session.get(RouterConfigurationRecord, configuration_id)
    if record is None:
        raise DomainError(
            "ROUTER_CONFIGURATION_NOT_FOUND",
            "The router configuration does not exist.",
            status_code=404,
        )
    return record


def list_router_configurations(session: Session) -> list[RouterConfigurationRecord]:
    return list(
        session.scalars(
            select(RouterConfigurationRecord).order_by(
                RouterConfigurationRecord.name,
                RouterConfigurationRecord.version,
            )
        )
    )


def freeze_router_configuration(
    session: Session, record: RouterConfigurationRecord
) -> RouterConfigurationRecord:
    if record.is_frozen:
        raise DomainError(
            "ROUTER_CONFIGURATION_IMMUTABLE",
            "The router configuration is already frozen.",
            status_code=409,
        )
    # Revalidate the stored JSON before it becomes a research dependency.
    RouterConfiguration.model_validate(record.configuration)
    record.frozen_at = datetime.now(UTC)
    session.flush()
    return record

