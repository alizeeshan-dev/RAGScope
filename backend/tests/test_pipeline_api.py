from fastapi.testclient import TestClient


def test_pipeline_configuration_api_create_list_freeze(client: TestClient) -> None:
    response = client.post(
        "/api/v1/pipeline-configurations",
        json={"name": "lexical-baseline", "version": 1, "retrieval_mode": "lexical"},
    )
    assert response.status_code == 201
    configuration_id = response.json()["id"]
    assert response.json()["frozen_at"] is None
    assert response.json()["configuration_hash"]

    listed = client.get("/api/v1/pipeline-configurations")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [configuration_id]

    frozen = client.post(f"/api/v1/pipeline-configurations/{configuration_id}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["frozen_at"] is not None

    duplicate_freeze = client.post(
        f"/api/v1/pipeline-configurations/{configuration_id}/freeze"
    )
    assert duplicate_freeze.status_code == 409
    assert duplicate_freeze.json()["error"]["code"] == "PIPELINE_CONFIGURATION_IMMUTABLE"
