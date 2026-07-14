from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_agent_persists_to_local_json_file(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles customer quote tasks",
            "capabilities": ["quote", "crm"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Quote Agent"
    assert body["capabilities"] == ["quote", "crm"]

    list_response = client.get("/api/v1/agents")
    assert list_response.status_code == 200
    assert list_response.json() == [body]
    assert "Quote Agent" in data_file.read_text()


def test_create_agent_accepts_tool_definitions(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "CRM Agent",
            "description": "Uses CRM tools",
            "capabilities": ["crm"],
            "tools": [
                {
                    "name": "crm_query",
                    "description": "Query customer information from CRM",
                    "type": "http",
                    "config": {
                        "method": "GET",
                        "url": "https://crm.example.com/customers/{customer_id}",
                    },
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "customer_id": {"type": "string"},
                        },
                    },
                }
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tools"][0]["name"] == "crm_query"
    assert body["tools"][0]["type"] == "http"
    assert body["tools"][0]["input_schema"]["properties"]["customer_id"]["type"] == "string"
