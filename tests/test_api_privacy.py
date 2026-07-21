from pathlib import Path

from fastapi.testclient import TestClient

from app.api.serialization import sanitize_task
from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    Artifact,
    SubTask,
    Task,
    TaskContext,
    TaskExecution,
    TaskRequestResponse,
    TaskRerunResponse,
    TaskRound,
    ToolCall,
    ToolExecutionResult,
    utc_now,
)
from app.main import create_app


SENSITIVE_TASK_VALUE = "task-private-placeholder"
SENSITIVE_AGENT_VALUE = "agent-private-placeholder"


def _sensitive_task() -> Task:
    now = utc_now()
    arguments = {"token": SENSITIVE_TASK_VALUE}
    subtask = SubTask(
        id="subtask_private",
        execution_id="execution_private",
        title="Review output",
        description="Review the generated output",
        assignee_type="human",
        assignee_user_id="root",
        assignee_user_name="Administrator",
        current_node=CurrentNode.HUMAN_EXECUTION,
        status=TaskStatus.RUNNING,
        tool_calls=[ToolCall(tool_name="private_tool", arguments=arguments)],
        tool_results=[
            ToolExecutionResult(
                tool_execution_id="tool_private",
                tool_name="private_tool",
                tool_type="http",
                arguments=arguments,
                success=True,
                result="created",
            )
        ],
        result_metadata={
            "private_note": SENSITIVE_TASK_VALUE,
            "decision": "pending",
        },
    )
    context = TaskContext(
        summary="Awaiting review",
        rounds=[TaskRound(round_index=1, subtasks=[subtask])],
    )
    artifact = Artifact(
        id="artifact_private",
        task_id="task_private",
        execution_id="execution_private",
        kind=ArtifactKind.TOOL_RESULT,
        source_type=ArtifactSourceType.TOOL_RESULT,
        source_id="tool_private",
        name="Public receipt",
        content="created",
        uri="file:///tmp/public-receipt.txt",
        checksum="public-checksum",
        validation_status=ArtifactValidationStatus.VALID,
        metadata={
            "arguments": arguments,
            "private_note": SENSITIVE_TASK_VALUE,
            "nested": {
                "private_token": SENSITIVE_TASK_VALUE,
                "safe_label": "receipt",
            },
            "path": "deliveries/public-receipt.txt",
        },
        created_at=now,
    )
    execution = TaskExecution(
        id="execution_private",
        task_id="task_private",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.HUMAN_EXECUTION,
        current_node=CurrentNode.HUMAN_EXECUTION,
        workflow_snapshot={
            "private_note": SENSITIVE_TASK_VALUE,
            "nested": {
                "private_token": SENSITIVE_TASK_VALUE,
                "safe_workflow_label": "review-flow",
            },
            "safe_workflow_name": "Receipt review",
        },
        context_snapshot=context.model_copy(deep=True),
        artifacts=[artifact.model_copy(deep=True)],
        created_at=now,
        started_at=now,
    )
    return Task(
        id="task_private",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Review a public receipt",
        request_metadata={
            "private_note": SENSITIVE_TASK_VALUE,
            "nested": {
                "private_token": SENSITIVE_TASK_VALUE,
                "safe_request_label": "receipt",
            },
            "container_metadata": (
                {
                    "private_tuple_value": SENSITIVE_TASK_VALUE,
                    "safe_tuple_label": "tuple",
                },
                (
                    {
                        "private_nested_tuple_value": SENSITIVE_TASK_VALUE,
                        "safe_nested_tuple_label": "nested-tuple",
                    },
                ),
            ),
            "set_values": {"set-a", "set-b"},
            "frozenset_values": frozenset({"frozen-a", "frozen-b"}),
            "safe_request_source": "business-system",
        },
        title="Review receipt",
        description="Review the generated receipt",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_EXECUTION,
        context=context,
        executions=[execution],
        active_execution_id=execution.id,
        artifacts=[artifact],
        created_at=now,
        updated_at=now,
    )


def _privacy_client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    client.app.state.task_store.save(_sensitive_task())
    return client


def _assert_execution_payload_is_public(payload: dict) -> None:
    workflow_snapshot = payload["workflow_snapshot"]
    assert "private_note" not in workflow_snapshot
    assert "private_token" not in workflow_snapshot["nested"]
    assert workflow_snapshot["nested"]["safe_workflow_label"] == "review-flow"
    assert workflow_snapshot["safe_workflow_name"] == "Receipt review"

    subtask = payload["context_snapshot"]["rounds"][0]["subtasks"][0]
    assert subtask["tool_calls"][0]["arguments"] == {"token": "[REDACTED]"}
    assert subtask["tool_results"][0]["arguments"] == {"token": "[REDACTED]"}
    assert "private_note" not in subtask["result_metadata"]
    assert subtask["result_metadata"]["decision"] == "pending"

    artifact = payload["artifacts"][0]
    assert artifact["metadata"]["arguments"] == {"token": "[REDACTED]"}
    assert "private_note" not in artifact["metadata"]
    assert "private_token" not in artifact["metadata"]["nested"]
    assert artifact["metadata"]["nested"]["safe_label"] == "receipt"
    assert artifact["metadata"]["path"] == "deliveries/public-receipt.txt"
    assert artifact["checksum"] == "public-checksum"
    assert artifact["uri"] == "file:///tmp/public-receipt.txt"


def _assert_task_payload_is_public(payload: dict) -> None:
    request_metadata = payload["request_metadata"]
    assert "private_note" not in request_metadata
    assert "private_token" not in request_metadata["nested"]
    assert request_metadata["nested"]["safe_request_label"] == "receipt"
    container_metadata = request_metadata["container_metadata"]
    assert "private_tuple_value" not in container_metadata[0]
    assert container_metadata[0]["safe_tuple_label"] == "tuple"
    assert "private_nested_tuple_value" not in container_metadata[1][0]
    assert container_metadata[1][0]["safe_nested_tuple_label"] == "nested-tuple"
    assert set(request_metadata["set_values"]) == {"set-a", "set-b"}
    assert set(request_metadata["frozenset_values"]) == {"frozen-a", "frozen-b"}
    assert request_metadata["safe_request_source"] == "business-system"
    _assert_execution_payload_is_public(payload["executions"][0])


def test_task_sanitizer_normalizes_json_serializable_containers_to_lists() -> None:
    sanitized = sanitize_task(_sensitive_task())
    request_metadata = sanitized.request_metadata

    assert isinstance(request_metadata["container_metadata"], list)
    assert isinstance(request_metadata["container_metadata"][1], list)
    assert isinstance(request_metadata["set_values"], list)
    assert isinstance(request_metadata["frozenset_values"], list)


def test_task_detail_returns_sanitized_public_projection(tmp_path: Path) -> None:
    client = _privacy_client(tmp_path)
    before = client.app.state.task_service.get_task("task_private").model_dump_json()

    response = client.get("/api/v1/tasks/task_private")

    assert response.status_code == 200
    assert SENSITIVE_TASK_VALUE not in response.text
    body = response.json()
    _assert_task_payload_is_public(body)
    assert client.app.state.task_service.get_task("task_private").model_dump_json() == before


def test_execution_polling_returns_sanitized_public_projection(tmp_path: Path) -> None:
    client = _privacy_client(tmp_path)
    before = client.app.state.task_service.get_task("task_private").model_dump_json()

    response = client.get("/api/v1/tasks/task_private/executions/execution_private")

    assert response.status_code == 200
    assert SENSITIVE_TASK_VALUE not in response.text
    _assert_execution_payload_is_public(response.json())
    assert client.app.state.task_service.get_task("task_private").model_dump_json() == before


def test_human_subtask_list_redacts_private_execution_fields(tmp_path: Path) -> None:
    client = _privacy_client(tmp_path)
    before = client.app.state.task_service.get_task("task_private").model_dump_json()

    response = client.get("/api/v1/subtasks/human")

    assert response.status_code == 200
    assert SENSITIVE_TASK_VALUE not in response.text
    body = response.json()[0]
    assert body["tool_calls"][0]["arguments"] == {"token": "[REDACTED]"}
    assert body["tool_results"][0]["arguments"] == {"token": "[REDACTED]"}
    assert "private_note" not in body["result_metadata"]
    assert body["result_metadata"]["decision"] == "pending"
    assert client.app.state.task_service.get_task("task_private").model_dump_json() == before


def test_all_task_response_routes_use_the_public_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _privacy_client(tmp_path)
    service = client.app.state.task_service
    task = service.get_task("task_private")
    task.assigned_agent_id = "agent_public"
    service.store.save(task)
    before = service.get_task("task_private").model_dump_json()
    monkeypatch.setattr(
        service,
        "create_request",
        lambda *_args, **_kwargs: TaskRequestResponse(
            request_id="request_private",
            tasks=[task],
        ),
    )
    monkeypatch.setattr(service, "confirm_task", lambda *_args, **_kwargs: task)
    monkeypatch.setattr(service, "submit_result", lambda *_args, **_kwargs: task)
    monkeypatch.setattr(service, "submit_subtask_result", lambda *_args, **_kwargs: task)
    monkeypatch.setattr(
        service,
        "create_rerun",
        lambda *_args, **_kwargs: TaskRerunResponse(
            task=task,
            execution=task.executions[0],
            replayed=True,
            execution_is_active=True,
        ),
    )

    responses = [
        client.get("/api/v1/tasks"),
        client.post("/api/v1/agents/agent_public/poll"),
        client.post(
            "/api/v1/tasks/requests",
            json={"source_type": "business_system", "content": "Create a task"},
        ),
        client.post(
            "/api/v1/tasks/task_private/confirm",
            json={"title": "Review receipt", "description": "Review receipt"},
        ),
        client.post(
            "/api/v1/tasks/task_private/result",
            json={"result_status": "succeeded", "output": "done"},
        ),
        client.post(
            "/api/v1/subtasks/subtask_private/result",
            json={"result_status": "succeeded", "output": "approved"},
        ),
        client.get("/api/v1/tasks/task_private/executions"),
        client.post(
            "/api/v1/tasks/task_private/executions",
            headers={"Idempotency-Key": "privacy-rerun-key"},
            json={
                "source_execution_id": "execution_private",
                "reason": "Retry privacy projection",
            },
        ),
    ]

    assert [response.status_code for response in responses] == [
        200,
        200,
        201,
        200,
        200,
        200,
        200,
        200,
    ]
    for response in responses:
        assert SENSITIVE_TASK_VALUE not in response.text

    task_payloads = [
        responses[0].json()[0],
        responses[1].json()[0],
        responses[2].json()["tasks"][0],
        responses[3].json(),
        responses[4].json(),
        responses[5].json(),
        responses[7].json()["task"],
    ]
    for payload in task_payloads:
        _assert_task_payload_is_public(payload)
    _assert_execution_payload_is_public(responses[6].json()[0])
    _assert_execution_payload_is_public(responses[7].json()["execution"])
    assert service.get_task("task_private").model_dump_json() == before


def test_agent_create_and_list_return_public_projection_without_mutating_registry(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    payload = {
        "name": "Public CRM Agent",
        "description": "Queries customer records",
        "capabilities": ["crm"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "execution_config": {
            "system_prompt": SENSITIVE_AGENT_VALUE,
            "model_name": "local-model",
        },
        "metadata": {"private_note": SENSITIVE_AGENT_VALUE},
        "tools": [
            {
                "name": "crm_query",
                "description": "Query CRM",
                "type": "http",
                "config": {"authorization": SENSITIVE_AGENT_VALUE},
                "input_schema": {"type": "object"},
            }
        ],
    }

    created = client.post("/api/v1/agents", json=payload)
    listed = client.get("/api/v1/agents")

    assert created.status_code == 201
    assert listed.status_code == 200
    for response in (created, listed):
        assert SENSITIVE_AGENT_VALUE not in response.text

    body = created.json()
    assert "execution_config" not in body
    assert "metadata" not in body
    assert body["input_schema"] == {"type": "object"}
    assert body["output_schema"] == {"type": "object"}
    assert body["tools"] == [
        {
            "name": "crm_query",
            "description": "Query CRM",
            "type": "http",
            "input_schema": {"type": "object"},
        }
    ]
    assert listed.json() == [body]

    internal = client.app.state.agent_registry.list_agents()[0]
    assert internal.execution_config.system_prompt == SENSITIVE_AGENT_VALUE
    assert internal.metadata["private_note"] == SENSITIVE_AGENT_VALUE
    assert internal.tools[0].config["authorization"] == SENSITIVE_AGENT_VALUE
