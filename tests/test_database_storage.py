from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import DEFAULT_DATABASE_URL
from app.core.enums import CurrentNode, SourceType, TaskStatus
from app.core.models import AgentCreate, RoundPlan, SubTask, Task, ToolCall, new_id, utc_now
from app.main import create_app
from app.services import storage as storage_module
from app.services.storage import DatabaseAgentRegistry, DatabaseTaskStore


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


def test_database_storage_cancels_unconfirmed_task_and_removes_rows(tmp_path: Path) -> None:
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
    assert client.get("/api/v1/tasks").json() == []

    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        task_count = connection.execute(text("select count(*) from tasks where id = :id"), {"id": task_id}).scalar_one()
        request_count = connection.execute(text("select count(*) from task_requests where id = :id"), {"id": request_id}).scalar_one()
        event_count = connection.execute(text("select count(*) from task_events where task_id = :id"), {"id": task_id}).scalar_one()

    assert task_count == 0
    assert request_count == 0
    assert event_count == 0


def test_create_app_uses_default_mysql_database_url(monkeypatch) -> None:
    captured_urls = []

    class FakeDatabaseAgentRegistry:
        def __init__(self, database_url):
            captured_urls.append(("agent", database_url))

    class FakeDatabaseTaskStore:
        def __init__(self, database_url):
            captured_urls.append(("task", database_url))

    class FakeDatabaseWorkflowRegistry:
        def __init__(self, database_url):
            captured_urls.append(("workflow", database_url))

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DISABLE_DEFAULT_DATABASE_URL", raising=False)
    monkeypatch.setattr("app.main.DatabaseAgentRegistry", FakeDatabaseAgentRegistry)
    monkeypatch.setattr("app.main.DatabaseTaskStore", FakeDatabaseTaskStore)
    monkeypatch.setattr("app.main.DatabaseWorkflowRegistry", FakeDatabaseWorkflowRegistry)

    create_app()

    assert captured_urls == [
        ("agent", DEFAULT_DATABASE_URL),
        ("task", DEFAULT_DATABASE_URL),
        ("workflow", DEFAULT_DATABASE_URL),
    ]


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
    assert tool_rows[0]["success"] == 1
    assert "Customer A" in tool_rows[0]["result_text"]
