from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_and_update_workflow_template_persists_definition(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))

    created = client.post(
        "/api/v1/workflows",
        json={
            "name": "Quote Approval",
            "description": "Create a quote and approve it",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "make_quote", "type": "agent", "title": "Make quote", "description": "Make quote"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "make_quote"},
                    {"from": "make_quote", "to": "end"},
                ],
            },
        },
    )

    assert created.status_code == 201
    workflow = created.json()
    assert workflow["name"] == "Quote Approval"
    assert workflow["definition"]["nodes"][1]["id"] == "make_quote"

    updated = client.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={
            "name": "Quote Approval Updated",
            "description": "Updated in place",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "approve_quote", "type": "human", "title": "Approve quote"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "approve_quote"},
                    {"from": "approve_quote", "to": "end"},
                ],
            },
        },
    )

    assert updated.status_code == 200
    reloaded = client.get(f"/api/v1/workflows/{workflow['id']}").json()
    assert reloaded["name"] == "Quote Approval Updated"
    assert reloaded["definition"]["nodes"][1]["id"] == "approve_quote"


def test_workflow_template_task_runs_agent_then_pauses_on_human_node(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote tasks",
            "capabilities": ["quote"],
        },
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Quote Approval",
            "description": "Create quote and approve it",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "make_quote",
                        "type": "agent",
                        "agent_id": agent["id"],
                        "title": "Make quote",
                        "description": "Make quote draft",
                    },
                    {
                        "id": "approve_quote",
                        "type": "human",
                        "title": "Approve quote",
                        "description": "Approve quote draft",
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "make_quote"},
                    {"from": "make_quote", "to": "approve_quote"},
                    {"from": "approve_quote", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: ([], "quote ready"))

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create quote through workflow",
            "metadata": {
                "execution_mode": "workflow_template",
                "workflow_id": workflow["id"],
            },
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Workflow quote", "description": "Create quote through workflow"},
    ).json()

    assert paused["task_status"] == "running"
    assert paused["current_node"] == "human_execution"
    assert paused["request_metadata"]["workflow_id"] == workflow["id"]
    assert paused["context"]["rounds"][0]["subtasks"][0]["id"].endswith("_make_quote")
    assert paused["context"]["rounds"][0]["subtasks"][0]["status"] == "succeeded"
    assert paused["context"]["rounds"][1]["subtasks"][0]["id"].endswith("_approve_quote")
    assert paused["context"]["rounds"][1]["subtasks"][0]["status"] == "running"

    resumed = client.post(
        f"/api/v1/subtasks/{paused['context']['rounds'][1]['subtasks'][0]['id']}/result",
        json={"result_status": "succeeded", "output": "quote approved", "should_complete": True},
    ).json()

    assert resumed["task_status"] == "succeeded"
    assert resumed["current_node"] == "completion_judge"
    assert "quote ready" in resumed["context"]["summary"]
    assert "quote approved" in resumed["context"]["summary"]
