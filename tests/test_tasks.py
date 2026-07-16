from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.core.models import TaskConfirm


def test_task_request_waits_for_human_confirmation(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户A报价任务",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["request_id"].startswith("req_")
    assert len(result["tasks"]) == 1
    task = result["tasks"][0]
    assert task["task_status"] == "running"
    assert task["current_node"] == "human_confirmation"
    assert task["title"] == "客户A报价任务"
    assert task["description"] == "Create a quote for customer A"
    assert task["content"] == "Create a quote for customer A"
    assert task["draft"]["title"] == "Create a quote for customer A"


def test_task_request_title_must_not_exceed_50_chars(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "超" * 51,
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 422


def test_task_request_uses_manual_task_name_for_task_title(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户交付报告任务",
            "content": "分析客户需求并生成交付报告",
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"][0]
    assert task["title"] == "客户交付报告任务"
    assert task["draft"]["title"] == "分析客户需求并生成交付报告"


def test_task_list_and_detail_are_scoped_to_normal_user(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    alice = client.post(
        "/api/v1/users",
        json={"name": "张三", "phone": "13800000001", "email": "alice@example.com", "role": "user"},
    ).json()
    bob = client.post(
        "/api/v1/users",
        json={"name": "李四", "phone": "13800000002", "email": "bob@example.com", "role": "user"},
    ).json()

    alice_task = client.post(
        "/api/v1/tasks/requests",
        headers={"X-User-Id": alice["id"]},
        json={
            "source_type": "business_system",
            "title": "张三发起任务",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]
    bob_task = client.post(
        "/api/v1/tasks/requests",
        headers={"X-User-Id": bob["id"]},
        json={
            "source_type": "business_system",
            "title": "李四发起任务",
            "content": "Review contract for customer B",
        },
    ).json()["tasks"][0]

    assert alice_task["created_by_user_id"] == alice["id"]
    assert alice_task["created_by_user_name"] == "张三"
    assert bob_task["created_by_user_id"] == bob["id"]

    admin_task_ids = {task["id"] for task in client.get("/api/v1/tasks").json()}
    assert {alice_task["id"], bob_task["id"]}.issubset(admin_task_ids)

    alice_task_ids = {task["id"] for task in client.get("/api/v1/tasks", headers={"X-User-Id": alice["id"]}).json()}
    assert alice_task_ids == {alice_task["id"]}
    assert client.get(f"/api/v1/tasks/{alice_task['id']}", headers={"X-User-Id": alice["id"]}).status_code == 200
    assert client.get(f"/api/v1/tasks/{bob_task['id']}", headers={"X-User-Id": alice["id"]}).status_code == 403


def test_unconfirmed_task_can_be_cancelled_and_removed_from_task_list(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer B",
        },
    ).json()["tasks"][0]

    response = client.delete(f"/api/v1/tasks/{created['id']}")

    assert response.status_code == 204
    assert client.get("/api/v1/tasks").json() == []


def test_no_workflow_human_subtask_defaults_to_root_assignee(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="人工审核",
                    description="需要人工审核当前任务",
                    assignee_type="human",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "人工审核任务",
            "content": "请人工审核方案",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "人工审核任务", "description": "请人工审核方案"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == "root"
    assert human_subtask["assignee_user_name"] == "管理员"
    assert human_subtask["assignee_role"] == "admin"

    assert client.get("/api/v1/subtasks/human?assignee_user_id=root").json()[0]["id"] == human_subtask["id"]
    assert client.get("/api/v1/subtasks/human?assignee_user_id=user_001").json() == []


def test_no_workflow_human_subtask_infers_registered_assignee(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    user = client.post("/api/v1/users", json={"name": "王大锤"}).json()

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="王大锤确认方案",
                    description="需要王大锤确认当前方案是否可行",
                    assignee_type="human",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户B交付方案评审",
            "content": "整理方案后需要王大锤确认",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户B交付方案评审", "description": "整理方案后需要王大锤确认"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == user["id"]
    assert human_subtask["assignee_user_name"] == "王大锤"
    assert human_subtask["assignee_role"] == "approver"
    assert client.get(f"/api/v1/subtasks/human?assignee_user_id={user['id']}").json()[0]["id"] == human_subtask["id"]


def test_no_workflow_human_subtask_ignores_missing_model_assignee_and_uses_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="人工确认方案",
                    description="请不存在的人确认当前方案",
                    assignee_type="human",
                    assignee_user_id="missing_user",
                    assignee_user_name="不存在的人",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户B交付方案评审",
            "content": "整理方案后需要不存在的人确认",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户B交付方案评审", "description": "整理方案后需要不存在的人确认"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == "root"
    assert human_subtask["assignee_user_name"] == "管理员"


def test_agent_subtask_failure_stops_whole_task(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Requirement Agent",
            "description": "Handles requirement query",
            "capabilities": ["requirements"],
        },
    ).json()

    def _plan(task, _agents):
        if task.loop_count > 0:
            raise AssertionError("planner must not run again after a failed subtask")
        return RoundPlan(
            should_continue=True,
            execution_mode="parallel",
            reason="collect prerequisite information",
            subtasks=[
                SubTask(
                    id="subtask_failure_stop",
                    title="查询客户需求",
                    description="查询客户需求和预算",
                    assigned_agent_id=agent["id"],
                )
            ],
        )

    def _execute(task, subtask, agent, tool_results):
        return [], ""

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "查询客户需求后生成方案"},
    ).json()["tasks"][0]
    result = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户方案", "description": "查询客户需求后生成方案"},
    ).json()

    assert result["task_status"] == "failed"
    assert result["current_node"] == "subtask_execution"
    assert result["loop_count"] == 1
    assert len(result["context"]["rounds"]) == 1
    failed_subtask = result["context"]["rounds"][0]["subtasks"][0]
    assert failed_subtask["status"] == "failed"
    assert failed_subtask["output"] == "Agent returned no output"
    assert "查询客户需求: Agent returned no output" in result["final_output"]
    assert [event["type"] for event in result["events"]][-2:] == ["subtask_failed", "task_failed"]


def test_task_request_does_not_use_intent_mock_when_system_fallback_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"), raise_server_exceptions=False)

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 500


def test_task_request_intent_recognition_uses_registered_agents(tmp_path: Path, monkeypatch) -> None:
    captured_agents = []

    def _recognize(content, agents):
        captured_agents.extend(agents)
        return [
            {
                "title": "Create quote for customer A",
                "description": "Prepare quote for customer A",
                "confidence": 0.91,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
            }
        ]

    monkeypatch.setattr("app.services.task_service.recognize_tasks_with_model", _recognize)
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote tasks",
            "capabilities": ["quote"],
        },
    ).json()
    client.post(
        "/api/v1/agents",
        json={
            "name": "Canvas Human Node",
            "description": "Only used by workflow canvas",
            "agent_type": "human",
            "capabilities": ["approval"],
        },
    )

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"][0]
    assert [item.id for item in captured_agents] == [agent["id"]]
    assert task["draft"]["suggested_assignee_type"] == "agent"
    assert task["draft"]["suggested_agent_id"] == agent["id"]
    assert task["assigned_agent_id"] == agent["id"]


def test_background_task_failure_is_persisted_on_task(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "后台失败任务",
            "content": "Run a task that fails in background",
        },
    ).json()["tasks"][0]
    service = client.app.state.task_service
    service.confirm_task_details(
        created["id"],
        payload=TaskConfirm(
            title="后台失败任务",
            description="Run a task that fails in background",
        ),
    )

    def _raise(_task):
        raise RuntimeError("model execution failed")

    monkeypatch.setattr(service, "_run_automatic_flow", _raise)

    result = service.run_confirmed_task(created["id"])

    assert result.task_status == "failed"
    assert result.current_node == "completion_judge"
    assert result.final_output == "model execution failed"
    assert result.events[-1].type == "task_failed"
    assert service.get_task(created["id"]).task_status == "failed"


def test_task_request_merges_identified_steps_into_one_main_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda content, agents: [
            {
                "title": "Create quote for customer A",
                "description": "Prepare quote for customer A",
                "confidence": 0.91,
            },
            {
                "title": "Review contract for customer B",
                "description": "Review contract risk for customer B",
                "confidence": 0.88,
            },
        ],
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for A; review contract for B",
        },
    )

    assert response.status_code == 201
    result = response.json()
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["current_node"] == "human_confirmation"
    assert tasks[0]["task_status"] == "running"
    assert "Create quote for customer A" in tasks[0]["draft"]["title"]
    assert "Review contract for customer B" in tasks[0]["draft"]["title"]


def test_confirm_task_runs_automatic_flow_until_success(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and CRM tasks",
            "capabilities": ["quote", "crm"],
        },
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer A",
            "description": "Prepare and send quote for customer A",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "succeeded"
    assert task["current_node"] == "completion_judge"
    assert task["assigned_agent_id"] is not None
    assert task["loop_count"] == 1
    event_types = [event["type"] for event in task["events"]]
    assert event_types[:5] == [
        "task_created",
        "intent_recognized",
        "human_confirmed",
        "dispatch_decided",
        "agent_executed",
    ]
    assert "context_updated" in event_types
    assert event_types[-1] == "completion_judged"
    assert task["context"]["summary"]


def test_confirm_task_can_return_before_automatic_flow_when_async_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]

    scheduled_task_ids = []

    def _capture_background_start(self, task_id):
        scheduled_task_ids.append(task_id)

    monkeypatch.setattr("app.services.task_service.TaskService.start_background_task", _capture_background_start)

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer A",
            "description": "Prepare and send quote for customer A",
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "running"
    assert task["current_node"] == "dispatch_decision"
    assert task["title"] == "Create a quote for customer A"
    assert task["description"] == "Create a quote for customer A"
    assert scheduled_task_ids == [created["id"]]
    assert [event["type"] for event in task["events"]][-2:] == [
        "human_confirmed",
        "async_execution_scheduled",
    ]


def test_agent_can_poll_tasks_assigned_to_it(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and CRM tasks",
            "capabilities": ["quote", "crm"],
        },
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer C",
        },
    ).json()["tasks"][0]
    client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer C",
            "description": "Prepare quote and update crm for customer C",
        },
    )

    response = client.post(f"/api/v1/agents/{agent['id']}/poll")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == created["id"]
    assert tasks[0]["assigned_agent_id"] == agent["id"]


def test_confirm_task_without_matching_agent_goes_to_human_and_finishes(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Review legal contract risk",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Review legal contract risk",
            "description": "No matching local agent should force human node",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "succeeded"
    assert task["current_node"] == "completion_judge"
    assert task["assigned_agent_id"] is None
    event_types = [event["type"] for event in task["events"]]
    assert "human_node_processed" in event_types


def test_multi_round_task_updates_context_before_next_subtask_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda content, agents: [
            {
                "draft_key": "collect_info",
                "title": "Collect customer info",
                "description": "Collect customer requirements",
                "confidence": 0.91,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
                "depends_on": [],
            },
            {
                "draft_key": "create_quote",
                "title": "Create quote",
                "description": "Create quote after requirements are ready",
                "confidence": 0.88,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
                "depends_on": ["collect_info"],
            },
        ],
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and customer requirements tasks",
            "capabilities": ["quote", "requirements"],
        },
    )
    seen_contexts = []

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask, new_id

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Collect prerequisite information first",
                subtasks=[
                    SubTask(
                        id=new_id("subtask"),
                        title="Collect customer info",
                        description="Collect customer requirements",
                        assigned_agent_id=agents[0].id,
                    )
                ],
            )
        if task.loop_count == 1 and "requirements ready" in task.context.summary:
            return RoundPlan(
                should_continue=True,
                reason="Use collected information to create quote",
                subtasks=[
                    SubTask(
                        id=new_id("subtask"),
                        title="Create quote",
                        description="Create quote after requirements are ready",
                        assigned_agent_id=agents[0].id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        seen_contexts.append(task.context.summary)
        if subtask.title == "Collect customer info":
            return [], "requirements ready"
        return [], f"quote created using context: {task.context.summary}"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created_task = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Collect requirements, then create a quote",
        },
    ).json()["tasks"][0]

    result = client.post(
        f"/api/v1/tasks/{created_task['id']}/confirm",
        json={
            "title": created_task["draft"]["title"],
            "description": created_task["draft"]["description"],
        },
    ).json()

    assert result["task_status"] == "succeeded"
    assert result["loop_count"] == 2
    assert len(result["context"]["rounds"]) == 2
    assert seen_contexts == ["", "requirements ready"]
    assert "quote created using context: requirements ready" in result["context"]["summary"]


def test_human_subtask_pauses_round_until_result_is_submitted(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote tasks",
            "capabilities": ["quote"],
        },
    ).json()

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need agent work and human approval",
                subtasks=[
                    SubTask(
                        id="subtask_agent_parallel",
                        title="Prepare quote",
                        description="Prepare quote draft",
                        assigned_agent_id=agent["id"],
                    ),
                    SubTask(
                        id="subtask_human_parallel",
                        title="Approve discount",
                        description="Human must approve the discount",
                        assignee_type="human",
                    ),
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        return [], "agent quote draft ready"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare quote and approve discount"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Prepare quote", "description": "Prepare quote and approve discount"},
    ).json()

    assert paused["task_status"] == "running"
    assert paused["current_node"] == "human_execution"
    subtasks = paused["context"]["rounds"][0]["subtasks"]
    assert subtasks[0]["status"] == "succeeded"
    assert subtasks[0]["output"] == "agent quote draft ready"
    assert subtasks[1]["status"] == "running"
    assert subtasks[1]["assignee_type"] == "human"

    human_tasks = client.get("/api/v1/subtasks/human").json()
    assert len(human_tasks) == 1
    assert human_tasks[0]["id"] == "subtask_human_parallel"


def test_human_subtask_result_resumes_task_flow(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need human approval",
                subtasks=[
                    SubTask(
                        id="subtask_human_resume",
                        title="Approve discount",
                        description="Human must approve the discount",
                        assignee_type="human",
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Approve discount"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Approve discount", "description": "Human must approve the discount"},
    ).json()
    assert paused["current_node"] == "human_execution"

    resumed = client.post(
        "/api/v1/subtasks/subtask_human_resume/result",
        json={"result_status": "succeeded", "output": "discount approved", "should_complete": True},
    ).json()

    assert resumed["task_status"] == "succeeded"
    assert resumed["current_node"] == "completion_judge"
    assert "discount approved" in resumed["context"]["summary"]
    human_subtask = resumed["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["status"] == "succeeded"
    assert human_subtask["output"] == "discount approved"


def test_human_subtask_result_can_resume_task_flow_async(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    started: list[str] = []

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need human approval",
                subtasks=[
                    SubTask(
                        id="subtask_human_async_resume",
                        title="Approve risk",
                        description="Human must approve the risk summary",
                        assignee_type="human",
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _capture_background_start(self, task_id):
        started.append(task_id)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.services.task_service.TaskService.start_background_task", _capture_background_start)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Approve risk"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Approve risk", "description": "Human must approve the risk summary"},
    ).json()
    assert paused["current_node"] == "human_execution"

    response = client.post(
        "/api/v1/subtasks/subtask_human_async_resume/result",
        json={
            "result_status": "succeeded",
            "output": "risk approved",
            "should_complete": True,
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["task_status"] == "running"
    assert submitted["current_node"] == "context_update"
    assert "risk approved" in submitted["context"]["summary"]
    assert started == [created["id"]]
    assert client.get("/api/v1/subtasks/human").json() == []
