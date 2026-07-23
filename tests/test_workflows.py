from pathlib import Path
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.enums import (
    ArtifactSourceType,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.model_client import AgentModelExecutionError
from app.core.models import (
    AgentCreate,
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowTemplate,
    utc_now,
)
from app.main import create_app
from app.services.completion_service import CompletionService
from app.services.storage import AgentRegistry
from app.workflows.template_runner import WorkflowTemplateRunner


def _running_workflow_task(task_id: str) -> Task:
    now = utc_now()
    execution = TaskExecution(
        id=f"execution_{task_id}",
        task_id=task_id,
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    )
    return Task(
        id=task_id,
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Run workflow",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        title="Run workflow",
        description="Run workflow",
        executions=[execution],
        active_execution_id=execution.id,
        created_at=now,
        updated_at=now,
    )


def _workflow_runner_context(
    tmp_path: Path,
    *,
    task_id: str,
    node_ids: list[str],
) -> tuple[AgentRegistry, WorkflowTemplate, Task]:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Workflow Agent",
            description="Runs workflow steps",
            capabilities=["workflow"],
        )
    )
    now = utc_now()
    workflow = WorkflowTemplate(
        id=f"workflow_{task_id}",
        name="Workflow runner test",
        definition=WorkflowDefinition(
            nodes=[
                {"id": "start", "type": "start"},
                *[
                    {
                        "id": node_id,
                        "type": "agent",
                        "agent_id": agent.id,
                        "title": node_id.replace("_", " ").title(),
                    }
                    for node_id in node_ids
                ],
                {"id": "end", "type": "end"},
            ],
            edges=[
                *[
                    {"from": "start", "to": node_id}
                    for node_id in node_ids
                ],
                *[
                    {"from": node_id, "to": "end"}
                    for node_id in node_ids
                ],
            ],
        ),
        created_at=now,
        updated_at=now,
    )
    return registry, workflow, _running_workflow_task(task_id)


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


def test_delete_workflow_template_removes_it_from_registry(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Disposable Workflow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"from": "start", "to": "end"}],
            },
        },
    ).json()

    response = client.delete(f"/api/v1/workflows/{workflow['id']}")

    assert response.status_code == 204
    assert client.get("/api/v1/workflows").json() == []
    assert client.delete(f"/api/v1/workflows/{workflow['id']}").status_code == 404


def test_workflow_confirmation_snapshots_initial_context(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Order Review",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"from": "start", "to": "end"}],
            },
        },
    ).json()
    monkeypatch.setattr(
        "app.services.task_service.TaskService.start_background_task",
        lambda self, task_id, expected_execution_id=None: None,
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "订单审核",
            "content": "我的订单是1100块，帮我审核一下",
            "metadata": {
                "execution_mode": "workflow_template",
                "workflow_id": workflow["id"],
            },
        },
    ).json()["tasks"][0]
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "订单审核",
            "description": "我的订单是1100块，帮我审核一下",
            "execution_mode": "async",
        },
    ).json()

    expected_summary = "任务名称：订单审核\n任务诉求：我的订单是1100块，帮我审核一下"
    assert confirmed["initial_context"]["summary"] == expected_summary
    assert confirmed["context"]["summary"] == expected_summary
    assert confirmed["executions"][0]["context_snapshot"] == confirmed["initial_context"]


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
    assert paused["context"]["rounds"][0]["subtasks"][0]["logical_key"] == "make_quote"
    assert len(paused["context"]["rounds"][0]["subtasks"][0]["id"]) <= 64
    assert paused["context"]["rounds"][0]["subtasks"][0]["status"] == "succeeded"
    assert paused["context"]["rounds"][1]["subtasks"][0]["logical_key"] == "approve_quote"
    assert len(paused["context"]["rounds"][1]["subtasks"][0]["id"]) <= 64
    assert paused["context"]["rounds"][1]["subtasks"][0]["status"] == "running"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_user_id"] == "user_001"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_user_name"] == "张三"
    assert paused["context"]["rounds"][1]["subtasks"][0]["assignee_role"] == "quote_approver"

    resumed = client.post(
        f"/api/v1/subtasks/{paused['context']['rounds'][1]['subtasks'][0]['id']}/result",
        json={"result_status": "succeeded", "output": "quote approved", "should_complete": True},
    ).json()

    assert resumed["task_status"] == "succeeded", resumed["completion_report"]
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


def test_workflow_template_ignores_unconnected_nodes(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    quote_agent = client.post(
        "/api/v1/agents",
        json={"name": "Quote Agent", "description": "Handles quotes", "capabilities": ["quote"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Ignore Unconnected Nodes",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "make_quote", "type": "agent", "agent_id": quote_agent["id"], "title": "Make quote"},
                    {
                        "id": "orphan_condition",
                        "type": "condition",
                        "title": "Orphan condition",
                        "config": {
                            "condition_options": [{"value": "approved", "content": "Can continue"}],
                        },
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "make_quote"},
                    {"from": "make_quote", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.judge_condition_with_model",
        lambda _task, _subtask: {
            "decision": "approved",
            "reason": "Should not be called for an unconnected node",
            "matched_source": "",
            "confidence": 1.0,
        },
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "孤立节点任务",
            "content": "Run only connected workflow nodes.",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "孤立节点任务", "description": "Run only connected workflow nodes."},
    ).json()

    executed_titles = [
        subtask["title"]
        for round_item in confirmed["context"]["rounds"]
        for subtask in round_item["subtasks"]
    ]
    assert executed_titles == ["Make quote"]
    assert confirmed["task_status"] == "succeeded"


def test_workflow_template_human_subtask_uses_handoff_instruction_as_review_document(
    tmp_path: Path,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json")
    client = TestClient(app)
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Human Review Instruction",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "price_review",
                        "type": "human",
                        "title": "人工确认",
                        "description": "人工查看上游结果并补充通过、驳回或备注信息。",
                        "config": {
                            "assignee_user_id": "root",
                            "assignee_user_name": "管理员",
                            "handoff_instruction": "订单价格大于1000块，请人工审核是否可以继续。",
                        },
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "price_review"},
                    {"from": "price_review", "to": "end"},
                ],
            },
        },
    ).json()

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "人工审核文案任务",
            "content": "订单价格1200块，帮我给人工审核一下。",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "人工审核文案任务", "description": "订单价格1200块，帮我给人工审核一下。"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["description"] == "订单价格大于1000块，请人工审核是否可以继续。"

    legacy_task = app.state.task_service.store.get(created["id"])
    assert legacy_task is not None
    legacy_task.context.rounds[0].subtasks[0].description = "人工查看上游结果并补充通过、驳回或备注信息。"
    app.state.task_service.store.save(legacy_task)

    human_queue = client.get("/api/v1/subtasks/human").json()
    queue_item = next(item for item in human_queue if item["id"] == human_subtask["id"])
    assert queue_item["description"] == "订单价格大于1000块，请人工审核是否可以继续。"


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
    assert resumed["completion_report"]["workflow_end_node_id"] == "end"


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
    assert resumed["completion_report"]["workflow_end_node_id"] == "end"


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
                            "condition_content": "如果人工确认通过则返回 approved；如果人工驳回则返回 rejected。",
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
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {
                "decision": "approved",
                "reason": "人工输出表示确认通过。",
                "matched_source": "latest_round.subtasks.approve.output",
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
    )
    monkeypatch.setattr(
        "app.services.task_service.TaskService.start_background_task",
        lambda self, task_id, expected_execution_id=None: None,
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
    assert resumed["completion_report"]["workflow_end_node_id"] == "end"


def test_workflow_template_async_human_result_reaches_end_node_after_condition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Condition Human End",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "price_condition",
                        "type": "condition",
                        "title": "条件判断",
                        "config": {
                            "condition_options": [
                                {"value": "v1", "content": "订单价格大于1000块"},
                                {"value": "v2", "content": "订单价格小于等于1000块"},
                            ]
                        },
                    },
                    {"id": "review_expensive_order", "type": "human", "title": "人工确认"},
                    {"id": "review_normal_order", "type": "human", "title": "人工确认"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "price_condition"},
                    {
                        "from": "price_condition",
                        "to": "review_expensive_order",
                        "condition": {"type": "decision", "value": "v1"},
                    },
                    {
                        "from": "price_condition",
                        "to": "review_normal_order",
                        "condition": {"type": "decision", "value": "v2"},
                    },
                    {"from": "review_expensive_order", "to": "end"},
                    {"from": "review_normal_order", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {
                "decision": "v1",
                "reason": "订单价格1100块，大于1000块。",
                "matched_source": "task_context",
                "confidence": 1.0,
            },
            ensure_ascii=False,
        ),
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "我的订单是1100块，帮我审核一下",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "条件节点流程判断",
            "description": "我的订单是1100块，帮我审核一下",
            "execution_mode": "async",
        },
    ).json()
    paused = client.app.state.task_service.run_confirmed_task(created["id"])
    human_subtask = paused.context.rounds[1].subtasks[0]

    submitted = client.post(
        f"/api/v1/subtasks/{human_subtask.id}/result",
        json={
            "result_status": "succeeded",
            "output": "确认",
            "metadata": {"decision": "approved"},
            "execution_mode": "async",
        },
    ).json()
    resumed = client.app.state.task_service.run_confirmed_task(created["id"])

    assert submitted["task_status"] == "running"
    assert confirmed["active_execution_id"] == resumed.active_execution_id
    assert resumed.task_status.value == "succeeded"
    assert resumed.current_node.value == "completion_judge"
    assert resumed.completion_report.workflow_end_node_id == "end"


def test_workflow_mixed_branch_merge_accepts_completed_unconditional_path() -> None:
    condition_subtask = SubTask(
        id="condition-subtask",
        logical_key="price_condition",
        title="Price condition",
        description="Choose order path",
        assignee_type="condition",
        status=TaskStatus.SUCCEEDED,
        result_metadata={"decision": "large_order"},
    )
    review_subtask = SubTask(
        id="review-subtask",
        logical_key="large_order_review",
        title="Large order review",
        description="Review large order",
        assignee_type="human",
        status=TaskStatus.SUCCEEDED,
    )
    edges = [
        WorkflowEdge.model_validate(
            {
                "from": "price_condition",
                "to": "merge",
                "condition": {"type": "decision", "value": "small_order"},
            }
        ),
        WorkflowEdge.model_validate(
            {"from": "large_order_review", "to": "merge", "condition": {}}
        ),
    ]

    assert WorkflowTemplateRunner._dependencies_ready(
        edges,
        {"price_condition", "large_order_review"},
        {
            "price_condition": condition_subtask,
            "large_order_review": review_subtask,
        },
    )


def test_workflow_mixed_branch_merge_waits_when_no_path_has_completed() -> None:
    condition_subtask = SubTask(
        id="condition-subtask",
        logical_key="price_condition",
        title="Price condition",
        description="Choose order path",
        assignee_type="condition",
        status=TaskStatus.SUCCEEDED,
        result_metadata={"decision": "large_order"},
    )
    edges = [
        WorkflowEdge.model_validate(
            {
                "from": "price_condition",
                "to": "merge",
                "condition": {"type": "decision", "value": "small_order"},
            }
        ),
        WorkflowEdge.model_validate(
            {"from": "large_order_review", "to": "merge", "condition": {}}
        ),
    ]

    assert not WorkflowTemplateRunner._dependencies_ready(
        edges,
        {"price_condition"},
        {"price_condition": condition_subtask},
    )


def test_workflow_template_condition_node_uses_intelligent_context(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    fix_agent = client.post(
        "/api/v1/agents",
        json={"name": "Fix Agent", "description": "Handles fixes", "capabilities": ["fix"]},
    ).json()
    release_agent = client.post(
        "/api/v1/agents",
        json={"name": "Release Agent", "description": "Handles releases", "capabilities": ["release"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Intelligent Condition",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "qa_review", "type": "human", "title": "测试复核"},
                    {
                        "id": "judge_qa",
                        "type": "condition",
                        "title": "判断测试结果",
                        "config": {
                            "condition_description": "判断测试结论",
                            "condition_options": [
                                {"value": "approved", "content": "测试通过、可以继续上线"},
                                {"value": "rejected", "content": "测试不通过或者需要返工"},
                            ],
                        },
                    },
                    {"id": "fix_bug", "type": "agent", "agent_id": fix_agent["id"], "title": "返工修复"},
                    {"id": "release", "type": "agent", "agent_id": release_agent["id"], "title": "上线发布"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "qa_review"},
                    {"from": "qa_review", "to": "judge_qa"},
                    {"from": "judge_qa", "to": "fix_bug", "condition": {"type": "decision", "value": "rejected"}},
                    {"from": "judge_qa", "to": "release", "condition": {"type": "decision", "value": "approved"}},
                    {"from": "fix_bug", "to": "end"},
                    {"from": "release", "to": "end"},
                ],
            },
        },
    ).json()

    observed_prompt = {}

    def _model_create(_system_prompt, user_prompt):
        observed_prompt.update(json.loads(user_prompt))
        return json.dumps(
            {
                "decision": "rejected",
                "reason": "最近一轮人工输出表示测试不通过，需要返工。",
                "matched_source": "latest_round.subtasks.qa_review.output",
                "confidence": 0.92,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", _model_create)
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "智能条件任务",
            "content": "测试结果决定是否上线。",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "智能条件任务", "description": "测试结果决定是否上线。"},
    ).json()
    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]

    resumed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "测试没有通过，登录问题还需要返工。",
            "should_complete": False,
            "metadata": {},
        },
    ).json()

    completed = [
        subtask
        for round_item in resumed["context"]["rounds"]
        for subtask in round_item["subtasks"]
        if subtask["status"] == "succeeded"
    ]
    completed_titles = [subtask["title"] for subtask in completed]
    condition_subtask = next(subtask for subtask in completed if subtask["title"] == "判断测试结果")
    assert condition_subtask["result_metadata"]["decision"] == "rejected"
    assert condition_subtask["result_metadata"]["matched_source"] == "latest_round.subtasks.qa_review.output"
    assert "返工修复" in completed_titles
    assert "上线发布" not in completed_titles
    assert observed_prompt["condition"]["condition_options"] == [
        {"value": "approved", "content": "测试通过、可以继续上线"},
        {"value": "rejected", "content": "测试不通过或者需要返工"},
    ]
    assert observed_prompt["condition"]["allowed_decisions"] == ["approved", "rejected"]
    assert observed_prompt["task_context"]["summary"]
    assert observed_prompt["latest_round"]["subtasks"][0]["output"] == "测试没有通过，登录问题还需要返工。"


def test_workflow_template_condition_node_receives_initial_task_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Initial Condition Context",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "judge_price",
                        "type": "condition",
                        "title": "判断订单价格",
                        "config": {
                            "condition_options": [
                                {"value": "high_price", "content": "订单价格大于1000块"},
                                {"value": "normal_price", "content": "订单价格小于等于1000块"},
                            ],
                        },
                    },
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "judge_price"},
                    {"from": "judge_price", "to": "end", "condition": {"type": "decision", "value": "high_price"}},
                ],
            },
        },
    ).json()

    observed_prompt = {}

    def _model_create(_system_prompt, user_prompt):
        observed_prompt.update(json.loads(user_prompt))
        return json.dumps(
            {
                "decision": "high_price",
                "reason": "订单价格1200块，大于1000块。",
                "matched_source": "task_context.summary",
                "confidence": 0.99,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", _model_create)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "价格判断任务",
            "content": "订单价格1200块，帮我给人工审核一下。",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "价格判断任务", "description": "订单价格1200块，帮我给人工审核一下。"},
    ).json()

    assert "订单价格1200块" in observed_prompt["task_context"]["summary"]
    assert confirmed["task_status"] == "succeeded"


def test_workflow_template_condition_node_fails_task_when_unable_to_judge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    release_agent = client.post(
        "/api/v1/agents",
        json={"name": "Release Agent", "description": "Handles releases", "capabilities": ["release"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Unable Condition",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "qa_review", "type": "human", "title": "测试复核"},
                    {
                        "id": "judge_qa",
                        "type": "condition",
                        "title": "判断测试结果",
                        "config": {
                            "condition_content": "如果测试通过则返回 approved；如果测试不通过则返回 rejected。",
                            "allowed_decisions": ["approved", "rejected"],
                            "default_decision": "approved",
                        },
                    },
                    {"id": "release", "type": "agent", "agent_id": release_agent["id"], "title": "上线发布"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "qa_review"},
                    {"from": "qa_review", "to": "judge_qa"},
                    {"from": "judge_qa", "to": "release", "condition": {"type": "decision", "value": "approved"}},
                    {"from": "release", "to": "end"},
                ],
            },
        },
    ).json()

    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {"decision": "unknown", "reason": "上下文不足，无法判断。", "confidence": 0.1},
            ensure_ascii=False,
        ),
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: ([], f"executed:{subtask.title}"),
    )

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "条件失败任务",
            "content": "测试结果不明确时不能继续上线。",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "条件失败任务", "description": "测试结果不明确时不能继续上线。"},
    ).json()
    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]

    resumed = client.post(
        f"/api/v1/subtasks/{human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "测试结果不清楚，暂时无法确认是否通过。",
            "should_complete": False,
            "metadata": {},
        },
    ).json()

    all_subtasks = [subtask for round_item in resumed["context"]["rounds"] for subtask in round_item["subtasks"]]
    condition_subtask = next(subtask for subtask in all_subtasks if subtask["title"] == "判断测试结果")
    completed_titles = [subtask["title"] for subtask in all_subtasks if subtask["status"] == "succeeded"]

    assert resumed["task_status"] == "failed"
    assert resumed["current_node"] == "completion_judge"
    assert condition_subtask["status"] == "failed"
    assert condition_subtask["output"] == "无法正常判断条件"
    assert condition_subtask["result_metadata"]["reason"] == "无法正常判断条件"
    assert "无法正常判断条件" in resumed["final_output"]
    assert "上线发布" not in completed_titles


def test_workflow_template_does_not_succeed_when_condition_leaves_no_path(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    quote_agent = client.post(
        "/api/v1/agents",
        json={"name": "Quote Agent", "description": "Handles quotes", "capabilities": ["quote"]},
    ).json()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Condition Missing Route",
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
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "approve"},
                    {
                        "from": "approve",
                        "to": "make_quote",
                        "condition": {"field": "approval_result", "operator": "eq", "value": "approved"},
                    },
                    {"from": "make_quote", "to": "end"},
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
            "content": "Run conditional workflow without a rejected branch",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Condition missing route", "description": "Run conditional workflow without a rejected branch"},
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

    assert resumed["task_status"] == "blocked"
    assert resumed["current_node"] == "completion_judge"
    assert "没有可继续执行的节点" in resumed["final_output"]
    assert resumed["completion_report"]["terminal_status"] == "blocked"
    assert resumed["completion_report"]["awaiting_human_decision"] is False
    assert resumed["completion_report"]["workflow_end_node_id"] is None


def test_workflow_completion_records_the_actual_ready_end_node(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Multiple End Workflow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "judge",
                        "type": "condition",
                        "title": "Judge",
                        "config": {"default_decision": "approved"},
                    },
                    {"id": "end_rejected", "type": "end"},
                    {"id": "end_approved", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "judge"},
                    {
                        "from": "judge",
                        "to": "end_rejected",
                        "condition": {"type": "decision", "value": "rejected"},
                    },
                    {
                        "from": "judge",
                        "to": "end_approved",
                        "condition": {"type": "decision", "value": "approved"},
                    },
                ],
            },
        },
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Run multiple end workflow",
            "metadata": {"execution_mode": "workflow_template", "workflow_id": workflow["id"]},
        },
    ).json()["tasks"][0]

    completed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Multiple end", "description": "Run multiple end workflow"},
    ).json()

    assert completed["task_status"] == "succeeded"
    assert completed["completion_report"]["workflow_end_node_id"] == "end_approved"


def test_workflow_runner_stops_after_agent_model_execution_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry, workflow, task = _workflow_runner_context(
        tmp_path,
        task_id="task_workflow_model_failure",
        node_ids=["failing_step"],
    )
    model_calls = 0

    def _execute(*_args):
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            raise AssertionError("failed workflow node must not be executed again")
        raise AgentModelExecutionError(attempts=3, last_error="workflow model failure")

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _execute,
    )

    result = WorkflowTemplateRunner(registry).run(task, workflow)

    assert result.task_status == TaskStatus.FAILED
    assert result.loop_count == 1
    assert model_calls == 1
    assert len(result.context.rounds) == 1
    assert result.context.rounds[0].round_index == 1
    assert len(result.context.rounds[0].subtasks) == 1
    assert result.context.rounds[0].subtasks[0].status == TaskStatus.FAILED
    event_types = [event.type for event in result.events]
    assert "context_updated" not in event_types
    assert "workflow_completed" not in event_types


def test_workflow_runner_preserves_successful_parallel_artifact_when_peer_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry, workflow, task = _workflow_runner_context(
        tmp_path,
        task_id="task_workflow_parallel_failure",
        node_ids=["successful_step", "failed_step"],
    )
    model_calls: list[str] = []

    def _execute(_task, subtask, _agent, _tool_results):
        model_calls.append(subtask.logical_key)
        if model_calls.count(subtask.logical_key) > 1:
            raise AssertionError("failed workflow node must not be executed again")
        if subtask.logical_key == "successful_step":
            return [], "successful output"
        raise AgentModelExecutionError(attempts=3, last_error="parallel model failure")

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _execute,
    )

    result = WorkflowTemplateRunner(registry).run(task, workflow)

    assert result.task_status == TaskStatus.FAILED
    assert result.loop_count == 1
    assert sorted(model_calls) == ["failed_step", "successful_step"]
    assert len(result.context.rounds) == 1
    subtasks = result.context.rounds[0].subtasks
    assert [subtask.status for subtask in subtasks] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
    ]
    successful_artifact = next(
        artifact
        for artifact in result.artifacts
        if artifact.source_type == ArtifactSourceType.SUBTASK_OUTPUT
        and artifact.source_id == subtasks[0].id
    )
    assert successful_artifact.content == "successful output"
    assert "context_updated" not in [event.type for event in result.events]


def test_workflow_completion_normalizes_delivery_content_before_evaluation_and_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = utc_now()
    task = Task(
        id="task_workflow_delivery_content",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare a workflow delivery",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        title="Workflow delivery",
        description="Prepare a workflow delivery",
        contract=TaskContract(
            goal="Prepare a workflow delivery",
            deliverable_goal="Markdown delivery",
            deliverable_kind="file",
            deliverable_format="markdown",
            deliverable_filename="workflow-delivery.md",
            success_criteria=[
                TaskContractItem(
                    id="criterion_workflow_delivery",
                    description="Workflow delivery is complete",
                )
            ],
            confirmed_at=now,
        ),
        created_at=now,
        updated_at=now,
    )
    task.context.summary = "merged workflow delivery"
    workflow = WorkflowTemplate(
        id="workflow_delivery_content",
        name="Workflow delivery",
        definition=WorkflowDefinition(nodes=[], edges=[]),
        created_at=now,
        updated_at=now,
    )
    completion_service = CompletionService()
    runner = WorkflowTemplateRunner(
        AgentRegistry(tmp_path / "agents.json"),
        completion_service=completion_service,
    )
    calls = []

    def _delivery_content(_task, output):
        calls.append(("delivery_content", output))
        return "normalized workflow delivery"

    def _evaluate(_task, output):
        calls.append(("evaluate_criteria", output))
        return []

    def _finalize(task_arg, *, output, criterion_results=None, **_kwargs):
        calls.append(("finalize", output, criterion_results))
        task_arg.task_status = TaskStatus.SUCCEEDED
        return SimpleNamespace(terminal_status=TaskStatus.SUCCEEDED)

    monkeypatch.setattr(completion_service, "delivery_content", _delivery_content)
    monkeypatch.setattr(completion_service, "evaluate_criteria", _evaluate)
    monkeypatch.setattr(completion_service, "finalize", _finalize)

    runner._complete_workflow(task, workflow, "end")

    assert calls == [
        ("delivery_content", "merged workflow delivery"),
        ("finalize", "normalized workflow delivery", None),
    ]


def test_workflow_completion_ignores_independent_human_acceptance_metadata(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            agent_file=tmp_path / "agents.json",
            workflow_file=tmp_path / "workflows.json",
        )
    )
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Acceptance Workflow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "judge",
                        "type": "condition",
                        "title": "Prepare decision evidence",
                        "config": {"default_decision": "approved"},
                    },
                    {"id": "end_approved", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "judge"},
                    {"from": "judge", "to": "end_approved"},
                ],
            },
        },
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Run workflow and wait for acceptance",
            "metadata": {
                "execution_mode": "workflow_template",
                "workflow_id": workflow["id"],
            },
        },
    ).json()["tasks"][0]

    pending = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Acceptance workflow",
            "description": "Run workflow and wait for acceptance",
            "contract": {
                "goal": "Run the workflow",
                "deliverable_goal": "Approved workflow result",
                "success_criteria": [
                    {
                        "id": "criterion_workflow_done",
                        "description": "Workflow reaches its end node",
                    }
                ],
                "requires_human_acceptance": True,
            },
        },
    ).json()
    artifact_ids = [artifact["id"] for artifact in pending["artifacts"]]

    assert pending["task_status"] == "succeeded"
    assert pending["current_node"] == "completion_judge"
    assert pending["completion_report"]["terminal_status"] == "succeeded"
    assert pending["completion_report"]["workflow_end_node_id"] == "end_approved"
    assert pending["completion_report"]["criterion_results"][0]["status"] == "passed"
    assert pending["completion_report"]["human_accepted"] is False
    assert pending["executions"][0]["finished_at"] is not None
    assert [artifact["id"] for artifact in pending["artifacts"]] == artifact_ids

    response = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "人工验收通过",
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
