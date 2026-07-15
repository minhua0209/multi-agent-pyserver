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


def test_workflow_template_task_snapshots_definition_when_request_is_created(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    quote_agent = client.post(
        "/api/v1/agents",
        json={"name": "Quote Agent", "description": "Handles quotes", "capabilities": ["quote"]},
    ).json()
    risk_agent = client.post(
        "/api/v1/agents",
        json={"name": "Risk Agent", "description": "Handles risk", "capabilities": ["risk"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Snapshot Workflow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "make_quote", "type": "agent", "agent_id": quote_agent["id"], "title": "Make quote"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "make_quote"},
                    {"from": "make_quote", "to": "end"},
                ],
            },
        },
    ).json()

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "快照任务",
            "content": "Run original workflow",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]

    client.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={
            "name": "Snapshot Workflow Updated",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "risk_check", "type": "agent", "agent_id": risk_agent["id"], "title": "Risk check"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "risk_check"},
                    {"from": "risk_check", "to": "end"},
                ],
            },
        },
    )

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "快照任务", "description": "Run original workflow"},
    ).json()

    snapshot_nodes = confirmed["request_metadata"]["workflow_definition"]["nodes"]
    completed_titles = [
        subtask["title"]
        for round_item in confirmed["context"]["rounds"]
        for subtask in round_item["subtasks"]
    ]
    assert [node["id"] for node in snapshot_nodes] == ["start", "make_quote", "end"]
    assert "Make quote" in completed_titles
    assert "Risk check" not in completed_titles


def test_workflow_template_routes_by_human_result_metadata(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    revise_agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Revise Agent",
            "description": "Handles revisions",
            "capabilities": ["revise"],
        },
    ).json()
    quote_agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quotes",
            "capabilities": ["quote"],
        },
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Conditional Approval",
            "description": "Route approved and rejected approvals",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "approve", "type": "human", "title": "Approve"},
                    {
                        "id": "make_quote",
                        "type": "agent",
                        "agent_id": quote_agent["id"],
                        "title": "Make quote",
                    },
                    {
                        "id": "revise_solution",
                        "type": "agent",
                        "agent_id": revise_agent["id"],
                        "title": "Revise solution",
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "approve"},
                    {
                        "from": "approve",
                        "to": "make_quote",
                        "condition": {"field": "approval_result", "operator": "eq", "value": "approved"},
                    },
                    {
                        "from": "approve",
                        "to": "revise_solution",
                        "condition": {"field": "approval_result", "operator": "eq", "value": "rejected"},
                    },
                    {"from": "make_quote", "to": "end"},
                    {"from": "revise_solution", "to": "end"},
                ],
            },
        },
    ).json()

    def _execute(_task, subtask, _agent, _tool_results):
        return [], f"executed:{subtask.title}"

    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Run conditional workflow",
            "metadata": {
                "execution_mode": "workflow_template",
                "workflow_id": workflow["id"],
            },
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Conditional workflow", "description": "Run conditional workflow"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    resumed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "approved",
            "should_complete": False,
            "metadata": {"approval_result": "approved"},
        },
    ).json()

    completed_titles = [
        subtask["title"]
        for round_item in resumed["context"]["rounds"]
        for subtask in round_item["subtasks"]
        if subtask["status"] == "succeeded"
    ]
    assert "Make quote" in completed_titles
    assert "Revise solution" not in completed_titles
    assert resumed["task_status"] == "succeeded"


def test_workflow_template_routes_rejected_human_result_to_revision(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    revise_agent = client.post(
        "/api/v1/agents",
        json={"name": "Revise Agent", "description": "Handles revisions", "capabilities": ["revise"]},
    ).json()
    quote_agent = client.post(
        "/api/v1/agents",
        json={"name": "Quote Agent", "description": "Handles quotes", "capabilities": ["quote"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Rejected Conditional Approval",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "approve", "type": "human", "title": "Approve"},
                    {"id": "make_quote", "type": "agent", "agent_id": quote_agent["id"], "title": "Make quote"},
                    {
                        "id": "revise_solution",
                        "type": "agent",
                        "agent_id": revise_agent["id"],
                        "title": "Revise solution",
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "approve"},
                    {
                        "from": "approve",
                        "to": "make_quote",
                        "condition": {"field": "approval_result", "operator": "eq", "value": "approved"},
                    },
                    {
                        "from": "approve",
                        "to": "revise_solution",
                        "condition": {"field": "approval_result", "operator": "eq", "value": "rejected"},
                    },
                    {"from": "make_quote", "to": "end"},
                    {"from": "revise_solution", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Run rejected conditional workflow",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Rejected conditional workflow", "description": "Run rejected conditional workflow"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    resumed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "rejected",
            "should_complete": False,
            "metadata": {"approval_result": "rejected"},
        },
    ).json()

    completed_titles = [
        subtask["title"]
        for round_item in resumed["context"]["rounds"]
        for subtask in round_item["subtasks"]
        if subtask["status"] == "succeeded"
    ]
    assert "Revise solution" in completed_titles
    assert "Make quote" not in completed_titles
    assert resumed["task_status"] == "succeeded"


def test_workflow_template_condition_node_routes_by_decision(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    revise_agent = client.post(
        "/api/v1/agents",
        json={"name": "Revise Agent", "description": "Handles revisions", "capabilities": ["revise"]},
    ).json()
    quote_agent = client.post(
        "/api/v1/agents",
        json={"name": "Quote Agent", "description": "Handles quotes", "capabilities": ["quote"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Condition Node Approval",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "approve", "type": "human", "title": "Approve"},
                    {
                        "id": "judge_approval",
                        "type": "condition",
                        "title": "Judge approval",
                        "config": {
                            "mode": "rule",
                            "source_node_id": "approve",
                            "field": "decision",
                            "allowed_decisions": ["approved", "rejected", "need_more_info"],
                            "default_decision": "need_more_info",
                        },
                    },
                    {"id": "make_quote", "type": "agent", "agent_id": quote_agent["id"], "title": "Make quote"},
                    {
                        "id": "revise_solution",
                        "type": "agent",
                        "agent_id": revise_agent["id"],
                        "title": "Revise solution",
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "approve"},
                    {"from": "approve", "to": "judge_approval"},
                    {
                        "from": "judge_approval",
                        "to": "make_quote",
                        "condition": {"type": "decision", "value": "approved"},
                    },
                    {
                        "from": "judge_approval",
                        "to": "revise_solution",
                        "condition": {"type": "decision", "value": "rejected"},
                    },
                    {"from": "make_quote", "to": "end"},
                    {"from": "revise_solution", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Run condition node workflow",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Condition node workflow", "description": "Run condition node workflow"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    resumed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "approved",
            "should_complete": False,
            "metadata": {"decision": "approved"},
        },
    ).json()

    completed = [
        subtask
        for round_item in resumed["context"]["rounds"]
        for subtask in round_item["subtasks"]
        if subtask["status"] == "succeeded"
    ]
    completed_titles = [subtask["title"] for subtask in completed]
    condition_subtask = next(subtask for subtask in completed if subtask["title"] == "Judge approval")
    assert condition_subtask["assignee_type"] == "condition"
    assert condition_subtask["result_metadata"]["decision"] == "approved"
    assert "Make quote" in completed_titles
    assert "Revise solution" not in completed_titles
    assert resumed["task_status"] == "succeeded"
