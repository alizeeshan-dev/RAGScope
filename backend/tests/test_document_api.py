from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_document_api_upload_parse_inspect_and_chunk(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        database_url="sqlite://",
        artifact_root=tmp_path / "artifacts",
        max_upload_bytes=4096,
    )
    app = create_app()

    def session_override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        corpus_response = client.post(
            "/api/v1/corpora",
            json={"name": "API corpus", "description": "fixture", "domain": "testing"},
        )
        assert corpus_response.status_code == 201
        corpus_id = corpus_response.json()["id"]
        version_response = client.post(
            f"/api/v1/corpora/{corpus_id}/versions",
            json={"version_label": "v1"},
        )
        assert version_response.status_code == 201
        version_id = version_response.json()["id"]
        markdown = (
            b"# Findings\n\nObserved result.\n\n"
            b"| Metric | Value |\n| --- | --- |\n| F1 | 0.9 |"
        )
        upload_response = client.post(
            f"/api/v1/corpus-versions/{version_id}/documents",
            files={"file": ("paper.md", markdown, "text/markdown")},
        )
        assert upload_response.status_code == 201
        document_id = upload_response.json()["id"]
        duplicate_response = client.post(
            f"/api/v1/corpus-versions/{version_id}/documents",
            files={"file": ("copy.md", markdown, "text/markdown")},
        )
        assert duplicate_response.status_code == 409
        assert duplicate_response.json()["error"]["code"] == "DUPLICATE_DOCUMENT"

        parse_response = client.post(f"/api/v1/documents/{document_id}/parse?wait=true")
        assert parse_response.status_code == 200
        assert parse_response.json()["parse_status"] == "ready"
        elements = client.get(f"/api/v1/documents/{document_id}/elements").json()
        assert [element["sequence_number"] for element in elements] == list(
            range(len(elements))
        )
        assert any(element["element_type"] == "table" for element in elements)

        chunk_response = client.post(
            f"/api/v1/corpus-versions/{version_id}/chunk?wait=true",
            json={"strategy": "fixed", "target_tokens": 8, "overlap_tokens": 2},
        )
        assert chunk_response.status_code == 200
        assert chunk_response.json()
        assert all(chunk["token_count"] <= 8 for chunk in chunk_response.json())

        artifacts = client.get(f"/api/v1/documents/{document_id}/artifacts").json()
        assert {artifact["artifact_type"] for artifact in artifacts} == {
            "original",
            "normalized-document",
        }
        original_id = next(
            artifact["id"] for artifact in artifacts if artifact["artifact_type"] == "original"
        )
        content_response = client.get(f"/api/v1/artifacts/{original_id}/content")
        assert content_response.content == markdown
        assert "storage_key" not in artifacts[0]

        documents = client.get(
            f"/api/v1/corpus-versions/{version_id}/documents"
        ).json()
        assert [document["id"] for document in documents] == [document_id]

        index_response = client.post(
            f"/api/v1/corpus-versions/{version_id}/index?wait=true"
        )
        assert index_response.status_code == 200
        assert index_response.json()["status"] == "succeeded"
        index_status = client.get(
            f"/api/v1/corpus-versions/{version_id}/index-status"
        ).json()
        assert {item["index_type"] for item in index_status} == {"lexical", "dense"}
        assert all(item["integrity_valid"] for item in index_status)

        lexical = client.post(
            f"/api/v1/corpus-versions/{version_id}/search/lexical",
            json={"query": "Observed result", "top_k": 3},
        )
        dense = client.post(
            f"/api/v1/corpus-versions/{version_id}/search/dense",
            json={"query": "Observed result", "top_k": 3},
        )
        assert lexical.status_code == dense.status_code == 200
        assert lexical.json() and dense.json()

        jobs = client.get("/api/v1/jobs").json()
        assert {job["job_type"] for job in jobs} == {
            "document_parsing",
            "chunk_generation",
            "build_indexes",
        }

        freeze = client.post(f"/api/v1/corpus-versions/{version_id}/freeze")
        assert freeze.status_code == 200
        assert freeze.json()["content_hash"]
        immutable_upload = client.post(
            f"/api/v1/corpus-versions/{version_id}/documents",
            files={"file": ("new.txt", b"new", "text/plain")},
        )
        immutable_delete = client.delete(f"/api/v1/documents/{document_id}")
        assert immutable_upload.json()["error"]["code"] == "CORPUS_VERSION_IMMUTABLE"
        assert immutable_delete.json()["error"]["code"] == "CORPUS_VERSION_IMMUTABLE"
