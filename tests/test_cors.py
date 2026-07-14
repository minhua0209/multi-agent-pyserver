from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_cors_preflight_allows_static_frontend_origin(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            agent_file=tmp_path / "agents.json",
            workflow_file=tmp_path / "workflows.json",
        )
    )

    response = client.options(
        "/api/v1/tasks",
        headers={
            "Origin": "http://127.0.0.1:5500",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "GET" in response.headers["access-control-allow-methods"]
