from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import DEFAULT_DATABASE_URL
from app.core.enums import (
    CriterionResultStatus,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    AgentCreate,
    CompletionReport,
    CriterionResult,
    RoundPlan,
    SubTask,
    Task,
    TaskContext,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    ToolCall,
    new_id,
    utc_now,
)
from app.main import create_app
from app.services import storage as storage_module
from app.services.storage import (
    DatabaseAgentRegistry,
    DatabaseTaskAttachmentStore,
    DatabaseTaskStore,
    InMemoryTaskStore,
)
from app.services.artifact_service import ArtifactService
from app.services.execution_service import ExecutionService


def test_database_agent_registry_persists_agents_across_instances(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    registry = DatabaseAgentRegistry(database_url)

    created = registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )

    reloaded_registry = DatabaseAgentRegistry(database_url)
    agents = reloaded_registry.list_agents()

    assert len(agents) == 1
    assert agents[0].id == created.id
    assert agents[0].name == "Quote Agent"
    assert agents[0].capabilities == ["quote"]


def test_database_task_store_persists_tasks_across_instances(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    store = DatabaseTaskStore(database_url)
    task = Task(
        id=new_id("task"),
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create a quote",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    saved = store.save(task)

    reloaded_store = DatabaseTaskStore(database_url)
    assert reloaded_store.get(saved.id) == saved
    assert reloaded_store.list() == [saved]


def test_database_task_store_restores_rerun_history_and_idempotency_across_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    first_client = TestClient(create_app(database_url=database_url))
    now = utc_now()
    contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[TaskContractItem(id="criterion_1", description="Reviewable")],
        confirmed_at=now,
    )
    source = TaskExecution(
        id="execution_1",
        task_id="task_rerun_db",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.SUCCEEDED,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.COMPLETION_JUDGE,
        context_snapshot=TaskContext(summary="done"),
        final_output="done",
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    task = Task(
        id="task_rerun_db",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        task_status=TaskStatus.SUCCEEDED,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=contract,
        context=TaskContext(summary="done"),
        initial_context=TaskContext(summary="initial"),
        executions=[source],
        active_execution_id=source.id,
        final_output="done",
        created_at=now,
        updated_at=now,
    )
    first_client.app.state.task_store.save(task)
    monkeypatch.setattr(
        first_client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    payload = {
        "source_execution_id": "execution_1",
        "reason": "Retry after restart",
        "execution_mode": "async",
    }
    first = first_client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "database-rerun-key"},
    )
    assert first.status_code == 201

    second_client = TestClient(create_app(database_url=database_url))
    restored = second_client.get(f"/api/v1/tasks/{task.id}").json()
    replay = second_client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "database-rerun-key"},
    )

    assert len(restored["executions"]) == 2
    restored_rerun = restored["executions"][1]
    assert restored_rerun["idempotency_key"] == "database-rerun-key"
    assert restored_rerun["request_fingerprint"].startswith("sha256:")
    assert restored_rerun["execution_mode"] == "async"
    assert restored_rerun["retry_of_execution_id"] == "execution_1"
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["execution"]["id"] == restored_rerun["id"]


def test_database_task_store_persists_confirmed_contract_across_app_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    first_client = TestClient(create_app(database_url=database_url))
    monkeypatch.setattr(
        first_client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    created = first_client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "title": "交付方案", "content": "生成实施方案"},
    ).json()["tasks"][0]

    confirmed = first_client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "交付方案",
            "description": "生成实施方案",
            "contract": {
                "goal": "明确实施路径",
                "deliverable_goal": "交付实施方案",
                "deliverable_kind": "file",
                "deliverable_format": "text",
                "deliverable_filename": "delivery.txt",
                "deliverable_requirements": [{"description": "包含里程碑"}],
                "success_criteria": [{"description": "可以进入评审"}],
                "requires_human_acceptance": True,
            },
            "execution_mode": "async",
        },
    )
    assert confirmed.status_code == 200
    confirmed_payload = confirmed.json()

    second_client = TestClient(create_app(database_url=database_url))
    reloaded = second_client.get(f"/api/v1/tasks/{created['id']}").json()

    assert reloaded["contract"] == confirmed_payload["contract"]
    assert reloaded["contract"]["deliverable_kind"] == "text"
    assert reloaded["contract"]["deliverable_format"] is None
    assert reloaded["contract"]["deliverable_filename"] == ""
    assert reloaded["contract"]["version"] == 2
    assert reloaded["contract"]["deliverable_requirements"] == []
    assert reloaded["contract"]["requires_human_acceptance"] is False
    assert [item["description"] for item in reloaded["contract"]["success_criteria"]] == [
        "包含里程碑",
        "可以进入评审",
    ]
    assert all(item["id"] for item in reloaded["contract"]["success_criteria"])
    assert reloaded["initial_context"] == confirmed_payload["initial_context"]
    assert reloaded["executions"] == confirmed_payload["executions"]
    assert reloaded["active_execution_id"] == confirmed_payload["active_execution_id"]
    assert reloaded["executions"][0]["context_snapshot"] == reloaded["context"]
    assert reloaded["executions"][0]["status"] == reloaded["task_status"]
    assert reloaded["executions"][0]["final_output"] == reloaded["final_output"]


def test_database_app_start_migrates_legacy_pending_acceptance_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_OUTPUT_DIR", str(tmp_path / "outputs"))
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    first_app = create_app(database_url=database_url)
    first_client = TestClient(first_app)
    created = first_client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Legacy pending acceptance"},
    ).json()["tasks"][0]
    service = first_app.state.task_service
    task = service.get_task(created["id"])
    now = utc_now()
    task.contract = TaskContract(
        goal="Prepare legacy delivery",
        deliverable_goal="Legacy reviewable file",
        deliverable_kind="file",
        deliverable_format="text",
        deliverable_filename="legacy.txt",
        deliverable_requirements=[
            TaskContractItem(
                id="requirement_summary",
                description="Contains a summary",
            )
        ],
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="The result is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=now,
    )
    task.current_node = CurrentNode.HUMAN_INTERVENTION
    actor = first_app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = now
    criterion_results = [
        CriterionResult(
            criterion_id=criterion_id,
            status=CriterionResultStatus.PASSED,
            evidence_text="Legacy reviewable output",
        )
        for criterion_id in ("requirement_summary", "criterion_reviewable")
    ]
    report = CompletionReport(
        id=new_id("completion"),
        execution_id=execution.id,
        terminal_status=TaskStatus.RUNNING,
        completion_reason="Awaiting required human acceptance",
        criterion_results=criterion_results,
        human_accepted=False,
        awaiting_human_decision=False,
        decided_by_type="system",
        decided_by_id="completion_service",
        decided_at=now,
        evidence_summary="human acceptance is required",
    )
    task.task_status = TaskStatus.RUNNING
    task.final_output = "Legacy reviewable output"
    task.completion_report = report
    execution.status = TaskStatus.RUNNING
    execution.current_node = CurrentNode.HUMAN_INTERVENTION
    execution.contract_snapshot = task.contract.model_copy(deep=True)
    execution.context_snapshot = task.context.model_copy(deep=True)
    execution.final_output = task.final_output
    execution.completion_report = report.model_copy(deep=True)
    service.store.save(task)

    second_client = TestClient(create_app(database_url=database_url))
    migrated = second_client.get(f"/api/v1/tasks/{task.id}").json()

    assert migrated["task_status"] == "succeeded"
    assert migrated["contract"]["deliverable_kind"] == "file"
    assert migrated["contract"]["deliverable_requirements"] == []
    assert migrated["contract"]["requires_human_acceptance"] is False
    assert [item["id"] for item in migrated["contract"]["success_criteria"]] == [
        "requirement_summary",
        "criterion_reviewable",
    ]
    assert [
        item["id"]
        for item in migrated["executions"][0]["contract_snapshot"][
            "deliverable_requirements"
        ]
    ] == ["requirement_summary"]
    assert (
        migrated["executions"][0]["contract_snapshot"][
            "requires_human_acceptance"
        ]
        is True
    )
    assert migrated["executions"][0]["finished_at"] is not None
    assert migrated["completion_report"]["terminal_status"] == "succeeded"
    assert [event["type"] for event in migrated["events"]].count(
        "criteria_only_migrated"
    ) == 1

    third_client = TestClient(create_app(database_url=database_url))
    reloaded = third_client.get(f"/api/v1/tasks/{task.id}").json()

    assert [event["type"] for event in reloaded["events"]].count(
        "criteria_only_migrated"
    ) == 1


def test_database_task_store_uses_mysql_safe_task_type_migration(tmp_path: Path, monkeypatch) -> None:
    captured_columns = []
    original_ensure_column = storage_module._ensure_column

    def _capture_column(engine, table_name: str, column_name: str, definition: str) -> None:
        captured_columns.append((table_name, column_name, definition))
        original_ensure_column(engine, table_name, column_name, definition)

    monkeypatch.setattr(storage_module, "_ensure_column", _capture_column)

    DatabaseTaskStore(f"sqlite:///{tmp_path / 'taskhub.db'}")

    task_type_definition = next(
        definition
        for table_name, column_name, definition in captured_columns
        if table_name == "tasks" and column_name == "task_type"
    )
    assert task_type_definition == "VARCHAR(32) NOT NULL DEFAULT 'auto_planning'"


def test_database_storage_marks_attachment_context_columns_as_longtext(tmp_path: Path, monkeypatch) -> None:
    captured = []

    def _capture_longtext_columns(engine, table_name: str, column_names: list[str]) -> None:
        captured.append((table_name, column_names))

    monkeypatch.setattr(storage_module, "_ensure_mysql_longtext_columns", _capture_longtext_columns)

    DatabaseTaskStore(f"sqlite:///{tmp_path / 'taskhub.db'}")
    DatabaseTaskAttachmentStore(f"sqlite:///{tmp_path / 'taskhub.db'}")

    assert ("tasks", ["payload", "context_summary", "final_output", "draft_json"]) in captured
    assert ("task_rounds", ["context_before", "context_after", "plan_json"]) in captured
    assert ("task_attachments", ["payload"]) in captured


def test_create_app_can_use_database_storage(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    first_client = TestClient(create_app(database_url=database_url))

    response = first_client.post(
        "/api/v1/agents",
        json={
            "name": "CRM Agent",
            "description": "Handles CRM tasks",
            "capabilities": ["crm"],
        },
    )

    assert response.status_code == 201
    second_client = TestClient(create_app(database_url=database_url))
    list_response = second_client.get("/api/v1/agents")
    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "CRM Agent"


def test_database_storage_soft_cancels_unconfirmed_task_and_retains_rows(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    client = TestClient(create_app(database_url=database_url))

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "待取消任务",
            "content": "Create a quote for customer A",
        },
    ).json()
    task_id = created["tasks"][0]["id"]
    request_id = created["request_id"]

    response = client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 204
    tasks = client.get("/api/v1/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["task_status"] == "cancelled"
    assert tasks[0]["completion_report"]["completion_reason"] == "Cancelled before confirmation"

    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        task_count = connection.execute(text("select count(*) from tasks where id = :id"), {"id": task_id}).scalar_one()
        request_count = connection.execute(text("select count(*) from task_requests where id = :id"), {"id": request_id}).scalar_one()
        event_count = connection.execute(text("select count(*) from task_events where task_id = :id"), {"id": task_id}).scalar_one()

    assert task_count == 1
    assert request_count == 1
    assert event_count >= 1


def test_create_app_uses_database_url_from_environment(monkeypatch) -> None:
    captured_urls = []
    database_url = "mysql+pymysql://user:password@db.local:3306/demo_db"

    class FakeDatabaseAgentRegistry:
        def __init__(self, database_url):
            captured_urls.append(("agent", database_url))

    class FakeDatabaseTaskStore:
        def __init__(self, database_url):
            captured_urls.append(("task", database_url))

        def list(self):
            return []

    class FakeDatabaseWorkflowRegistry:
        def __init__(self, database_url):
            captured_urls.append(("workflow", database_url))

    class FakeDatabaseUserRegistry:
        def __init__(self, database_url):
            captured_urls.append(("user", database_url))

    class FakeDatabaseTaskAttachmentStore:
        def __init__(self, database_url):
            captured_urls.append(("attachment", database_url))

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("DISABLE_DEFAULT_DATABASE_URL", raising=False)
    monkeypatch.setattr("app.main.DatabaseAgentRegistry", FakeDatabaseAgentRegistry)
    monkeypatch.setattr("app.main.DatabaseTaskStore", FakeDatabaseTaskStore)
    monkeypatch.setattr("app.main.DatabaseWorkflowRegistry", FakeDatabaseWorkflowRegistry)
    monkeypatch.setattr("app.main.DatabaseUserRegistry", FakeDatabaseUserRegistry)
    monkeypatch.setattr("app.main.DatabaseTaskAttachmentStore", FakeDatabaseTaskAttachmentStore)

    create_app()

    assert captured_urls == [
        ("agent", database_url),
        ("task", database_url),
        ("workflow", database_url),
        ("user", database_url),
        ("attachment", database_url),
    ]


def test_create_app_uses_in_memory_tasks_without_database_url(monkeypatch) -> None:
    captured_urls = []

    class FakeDatabaseStorage:
        def __init__(self, database_url):
            captured_urls.append(database_url)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DISABLE_DEFAULT_DATABASE_URL", raising=False)
    monkeypatch.setattr("app.main.DatabaseAgentRegistry", FakeDatabaseStorage)
    monkeypatch.setattr("app.main.DatabaseTaskStore", FakeDatabaseStorage)
    monkeypatch.setattr("app.main.DatabaseWorkflowRegistry", FakeDatabaseStorage)
    monkeypatch.setattr("app.main.DatabaseUserRegistry", FakeDatabaseStorage)
    monkeypatch.setattr("app.main.DatabaseTaskAttachmentStore", FakeDatabaseStorage)

    app = create_app()

    assert DEFAULT_DATABASE_URL is None
    assert captured_urls == []
    assert isinstance(app.state.task_store, InMemoryTaskStore)


def test_create_app_database_storage_persists_workflow_templates(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    first_client = TestClient(create_app(database_url=database_url))
    created = first_client.post(
        "/api/v1/workflows",
        json={
            "name": "Quote Workflow",
            "description": "Persisted workflow",
            "definition": {
                "nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
                "edges": [{"from": "start", "to": "end"}],
            },
        },
    ).json()

    second_client = TestClient(create_app(database_url=database_url))
    reloaded = second_client.get(f"/api/v1/workflows/{created['id']}").json()

    assert reloaded["name"] == "Quote Workflow"
    assert reloaded["definition"]["edges"][0]["from"] == "start"


def test_database_storage_persists_structured_task_flow_tables(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    client = TestClient(create_app(database_url=database_url))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "CRM Agent",
            "description": "Handles CRM tasks",
            "capabilities": ["crm"],
            "tools": [
                {
                    "name": "crm_query",
                    "description": "Query customer information from CRM",
                    "type": "mock",
                    "config": {"response": '{"customer_name": "Customer A", "level": "vip"}'},
                }
            ],
        },
    ).json()

    def _plan(task, agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need CRM data",
                subtasks=[
                    SubTask(
                        id="subtask_structured",
                        title="Query CRM",
                        description="Query customer_a from CRM",
                        assigned_agent_id=agent["id"],
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        if not tool_results:
            return [ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"})], ""
        return [], f"Prepared quote for {tool_results[0].result}"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Query CRM and prepare quote",
            "metadata": {"external_id": "biz_001"},
        },
    ).json()
    task = created["tasks"][0]
    result = client.post(
        f"/api/v1/tasks/{task['id']}/confirm",
        json={"title": "Query CRM", "description": "Query CRM and prepare quote"},
    ).json()

    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        request_row = connection.execute(text("select * from task_requests where id = :id"), {"id": created["request_id"]}).mappings().one()
        task_row = connection.execute(text("select * from tasks where id = :id"), {"id": task["id"]}).mappings().one()
        round_rows = connection.execute(text("select * from task_rounds where task_id = :id"), {"id": task["id"]}).mappings().all()
        subtask_rows = connection.execute(text("select * from subtasks where task_id = :id"), {"id": task["id"]}).mappings().all()
        event_rows = connection.execute(text("select event_type from task_events where task_id = :id"), {"id": task["id"]}).mappings().all()
        snapshot_rows = connection.execute(text("select snapshot_type from task_snapshots where task_id = :id"), {"id": task["id"]}).mappings().all()
        tool_rows = connection.execute(text("select * from tool_executions where task_id = :id"), {"id": task["id"]}).mappings().all()

    assert result["task_status"] == "succeeded"
    assert request_row["status"] == "succeeded"
    assert request_row["source_type"] == "business_system"
    assert "biz_001" in request_row["metadata_json"]
    assert task_row["status"] == "succeeded"
    assert task_row["current_node"] == "completion_judge"
    assert task_row["request_id"] == created["request_id"]
    assert "Prepared quote" in task_row["context_summary"]
    assert len(round_rows) == 1
    assert round_rows[0]["round_index"] == 1
    assert round_rows[0]["reason"] == "Need CRM data"
    assert len(subtask_rows) == 1
    assert subtask_rows[0]["status"] == "succeeded"
    assert subtask_rows[0]["assigned_agent_id"] == agent["id"]
    assert "Prepared quote" in subtask_rows[0]["output"]
    assert [row["event_type"] for row in event_rows] == [
        "task_created",
        "intent_recognized",
        "human_confirmed",
        "dispatch_decided",
        "agent_executed",
        "context_updated",
        "completion_judged",
        "completion_judged",
    ]
    assert {row["snapshot_type"] for row in snapshot_rows} >= {
        "dispatch_output",
        "subtask_execution_output",
        "context_update",
    }
    assert len(tool_rows) == 1
    assert tool_rows[0]["tool_name"] == "crm_query"
    assert tool_rows[0]["tool_type"] == "mock"
    assert tool_rows[0]["success"] == 1
    assert "Customer A" in tool_rows[0]["result_text"]


def test_database_task_store_restores_artifacts_across_instances(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    store = DatabaseTaskStore(database_url)
    now = utc_now()
    contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[TaskContractItem(id="criterion_1", description="Reviewable")],
        confirmed_at=now,
        legacy_inferred=True,
    )
    execution = TaskExecution(
        id="execution_artifacts",
        task_id="task_artifacts",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    )
    task = Task(
        id="task_artifacts",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=contract,
        executions=[execution],
        active_execution_id=execution.id,
        created_at=now,
        updated_at=now,
    )
    ArtifactService().register_task_output(task, "Persisted delivery")
    ExecutionService().sync_projection(task)

    saved = store.save(task)
    reloaded = DatabaseTaskStore(database_url).get(task.id)

    assert reloaded is not None
    assert reloaded.artifacts == saved.artifacts
    assert reloaded.executions[0].artifacts == saved.artifacts
