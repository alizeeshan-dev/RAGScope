from collections.abc import Generator
from pathlib import Path

import pytest
from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db_session:
        yield db_session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(session: Session, tmp_path: Path) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://",
        artifact_root=tmp_path / "artifacts",
        job_api_default_wait=True,
        generation_provider="fake",
        embedding_provider="fake",
    )
    with TestClient(app) as test_client:
        yield test_client
