from uuid import uuid4

from fastapi.testclient import TestClient


def test_create_and_inspect_corpus_and_draft_version(client: TestClient) -> None:
    corpus_response = client.post(
        "/api/v1/corpora",
        json={"name": "Scientific datasets", "description": "Papers", "domain": "NLP"},
    )
    assert corpus_response.status_code == 201
    corpus_id = corpus_response.json()["id"]

    version_response = client.post(
        f"/api/v1/corpora/{corpus_id}/versions",
        json={
            "version_label": "v1",
            "parser_configuration": {"adapter": "docling", "version": "1"},
            "chunker_configuration": {"strategy": "fixed", "size": 200},
            "embedding_configuration": {"provider": "fake", "dimension": 64},
        },
    )
    assert version_response.status_code == 201
    assert version_response.json()["status"] == "draft"
    assert version_response.json()["content_hash"] is None

    detail = client.get(f"/api/v1/corpora/{corpus_id}")
    assert detail.status_code == 200
    assert [version["version_label"] for version in detail.json()["versions"]] == ["v1"]


def test_duplicate_version_label_has_stable_error(client: TestClient) -> None:
    corpus_id = client.post("/api/v1/corpora", json={"name": "C"}).json()["id"]
    route = f"/api/v1/corpora/{corpus_id}/versions"
    assert client.post(route, json={"version_label": "v1"}).status_code == 201
    response = client.post(route, json={"version_label": "v1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CORPUS_VERSION_LABEL_CONFLICT"


def test_freeze_rejects_unready_version(client: TestClient) -> None:
    corpus_id = client.post("/api/v1/corpora", json={"name": "C"}).json()["id"]
    version_id = client.post(
        f"/api/v1/corpora/{corpus_id}/versions", json={"version_label": "v1"}
    ).json()["id"]
    response = client.post(f"/api/v1/corpus-versions/{version_id}/freeze")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CORPUS_VERSION_NOT_READY"


def test_missing_resources_use_explicit_errors(client: TestClient) -> None:
    response = client.get(f"/api/v1/corpus-versions/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CORPUS_VERSION_NOT_FOUND"
