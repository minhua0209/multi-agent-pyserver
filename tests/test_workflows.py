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
                        "config": {
                            "assignee_user_id": "user_001",
                            "assignee_user_name": "张三",
                            "assignee_role": "quote_approver",
                        },
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

    assert created["task_type"] == "manual_orchestration"

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Workflow quote", "description": "Create quote through workflow"},
    ).json()

    assert paused["task_type"] == "manual_orchestration"
    assert paused["task_status"] == "running"
    assert paused["current_node"] == "human_execution"
    assert paused["request_metadata"]["workflow_id"] == workflow["id"]
    assert paused["context"]["rounds"][0]["subtasks"][0]["id"].endswith("_make_quote")
    assert paused["context"]["rounds"][0]["subtasks"][0]["status"] == "succeeded"
    assert paused["context"]["rounds"][1]["subtasks"][0]["id"].endswith("_approve_quote")
    assert paused["context"]["rounds"][1]["subtasks"][0]["status"] == "running"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_user_id"] == "user_001"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_user_name"] == "张三"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_role"] == "quote_approver"

    resumed = client.post(
        f"/api/v1/subtasks/{paused['context']['rounds'][1]['subtasks'][0]['id']}/result",
        json={"result_status": "succeeded", "output": "quote approved", "should_complete": True},
    ).json()

    assert resumed["task_status"] == "succeeded"
    assert resumed["current_node"] == "completion_judge"
    assert "quote ready" in resumed["context"]["summary"]
    assert "quote approved" in resumed["context"]["summary"]


def test_human_subtask_permissions_follow_assigned_user(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    assignee = client.post(
        "/api/v1/users",
        json={"name": "张三", "phone": "13800000001", "email": "zhangsan@example.com", "role": "user"},
    ).json()
    other_user = client.post(
        "/api/v1/users",
        json={"name": "李四", "phone": "13800000002", "email": "lisi@example.com", "role": "user"},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Assigned Human Review",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "review",
                        "type": "human",
                        "title": "人工确认",
                        "config": {
                            "assignee_user_id": assignee["id"],
                            "assignee_user_name": assignee["name"],
                            "assignee_role": assignee["role"],
                            "handoff_instruction": "请确认交付结果是否通过。",
                        },
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "review"},
                    {"from": "review", "to": "end"},
                ],
            },
        },
    ).json()

    created = client.post(
        "/api/v1/tasks/requests",
        headers={"X-User-Id": other_user["id"]},
        json={
            "source_type": "business_system",
            "title": "人工确认任务",
            "content": "Run assigned workflow",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        headers={"X-User-Id": other_user["id"]},
        json={"title": "人工确认任务", "description": "Run assigned workflow"},
    ).json()
    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]

    assignee_queue = client.get("/api/v1/subtasks/human", headers={"X-User-Id": assignee["id"]}).json()
    other_queue = client.get("/api/v1/subtasks/human", headers={"X-User-Id": other_user["id"]}).json()
    assert [item["id"] for item in assignee_queue] == [human_subtask["id"]]
    assert other_queue == []

    forbidden = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        headers={"X-User-Id": other_user["id"]},
        json={"result_status": "succeeded", "output": "不该被接受", "should_complete": True},
    )
    assert forbidden.status_code == 403

    allowed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        headers={"X-User-Id": assignee["id"]},
        json={"result_status": "succeeded", "output": "确认通过", "should_complete": True},
    )
    assert allowed.status_code == 200
    assert allowed.json()["task_status"] == "succeeded"


def test_workflow_template_request_skips_intent_fallback_when_system_fallback_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    monkeypatch.setattr("app.services.task_service.recognize_tasks_with_model", lambda _content, _agents: [])
    client = TestClient(
        create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"),
        raise_server_exceptions=False,
    )
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Template Flow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"from": "start", "to": "end"}],
            },
        },
    ).json()

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "模板任务",
            "content": "Run workflow template without intent fallback",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"][0]
    assert task["current_node"] == "human_confirmation"
    assert task["title"] == "模板任务"
    assert task["draft"]["title"] == "模板任务"
    assert task["request_metadata"]["workflow_id"] == workflow["id"]
    assert task["request_metadata"]["workflow_definition"]["nodes"][0]["id"] == "start"


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
