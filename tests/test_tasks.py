from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_task_request_waits_for_human_confirmation(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
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
    assert task["draft"]["title"] == "Create a quote for customer A"


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
