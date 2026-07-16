import json

import pytest

from app.core.model_client import (
    OpenAIResponsesClient,
    dispatch_with_model,
    execute_subtask_with_tools_model,
    judge_completion_with_model,
    recognize_task_with_model,
    recognize_tasks_with_model,
)
from app.core.models import Agent, AgentTool, SubTask, Task, ToolExecutionResult, utc_now
from app.core.enums import CurrentNode, SourceType, TaskStatus


def test_responses_client_extracts_output_text() -> None:
    client = OpenAIResponsesClient()

    text = client.extract_text({"output_text": '{"complete": true}'})

    assert text == '{"complete": true}'


def test_responses_client_extracts_nested_text() -> None:
    client = OpenAIResponsesClient()

    text = client.extract_text(
        {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"agent_id": "agent_1"}',
                        }
                    ]
                }
            ]
        }
    )

    assert text == '{"agent_id": "agent_1"}'


def test_responses_client_extracts_chat_completion_content() -> None:
    client = OpenAIResponsesClient()

    text = client.extract_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"ok": true}',
                    }
                }
            ]
        }
    )

    assert text == '{"ok": true}'


def test_responses_client_reads_api_key_from_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured_headers["authorization"] = req.get_header("Authorization")
        return FakeResponse()

    client = OpenAIResponsesClient(url="http://model.test/v1/chat/completions", model="test-model")
    monkeypatch.setenv("MODEL_API_KEY", "runtime-test-key")
    monkeypatch.setattr("app.core.model_client.request.urlopen", fake_urlopen)

    assert client.create("system", "user") == "ok"
    assert captured_headers["authorization"] == "Bearer runtime-test-key"


def test_responses_client_uses_configured_max_output_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_payload: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured_payload.update(json.loads(req.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr("app.core.model_client.MAX_OUTPUT_TOKENS", 4096)
    monkeypatch.setattr("app.core.model_client.request.urlopen", fake_urlopen)

    client = OpenAIResponsesClient(url="http://model.test/v1/chat/completions", model="test-model")

    assert client.create("system", "user") == "ok"
    assert captured_payload["max_tokens"] == 4096


def test_model_intent_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "title": "Create quote",
                "description": "Prepare quote for customer",
                "confidence": 0.81,
            }
        ),
    )

    draft = recognize_task_with_model("Create a quote", [])

    assert draft is not None
    assert draft.title == "Create quote"
    assert draft.description == "Prepare quote for customer"
    assert draft.confidence == 0.81


def test_model_intent_parses_multiple_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "tasks": [
                    {
                        "title": "Create quote",
                        "description": "Prepare quote for customer",
                        "confidence": 0.81,
                    },
                    {
                        "title": "Review contract",
                        "description": "Review customer contract",
                        "confidence": 0.76,
                    },
                ]
            }
        ),
    )

    drafts = recognize_tasks_with_model("Create a quote and review a contract", [])

    assert [draft.title for draft in drafts] == ["Create quote", "Review contract"]


def test_model_intent_accepts_suggested_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Agent(
        id="agent_quote",
        name="Quote Agent",
        description="Handles quote work",
        capabilities=["quote"],
        created_at=utc_now(),
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "tasks": [
                    {
                        "title": "Create quote",
                        "description": "Prepare quote for customer",
                        "confidence": 0.81,
                        "suggested_assignee_type": "agent",
                        "suggested_agent_id": "agent_quote",
                    }
                ]
            }
        ),
    )

    drafts = recognize_tasks_with_model("Create a quote", [agent])

    assert drafts[0].suggested_assignee_type == "agent"
    assert drafts[0].suggested_agent_id == "agent_quote"


def test_model_intent_parses_task_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "tasks": [
                    {
                        "draft_key": "collect_info",
                        "title": "Collect customer info",
                        "description": "Collect customer requirements",
                        "confidence": 0.91,
                        "depends_on": [],
                    },
                    {
                        "draft_key": "create_quote",
                        "title": "Create quote",
                        "description": "Create quote after requirements are ready",
                        "confidence": 0.88,
                        "depends_on": ["collect_info"],
                    },
                ]
            }
        ),
    )

    drafts = recognize_tasks_with_model("Collect requirements, then create a quote", [])

    assert drafts[0].draft_key == "collect_info"
    assert drafts[0].depends_on == []
    assert drafts[1].draft_key == "create_quote"
    assert drafts[1].depends_on == ["collect_info"]


def test_model_dispatch_selects_registered_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Agent(
        id="agent_quote",
        name="Quote Agent",
        description="Handles quote work",
        capabilities=["quote"],
        created_at=utc_now(),
    )
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create a quote",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        title="Create a quote",
        description="Prepare quote",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: '{"assignee_type":"agent","agent_id":"agent_quote"}',
    )

    selected = dispatch_with_model(task, [agent])

    assert selected == agent


def test_model_completion_judge_parses_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create a quote",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: '{"complete": true}',
    )

    assert judge_completion_with_model(task, "done") is True


def test_model_subtask_execution_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        tools=[AgentTool(name="crm_query", type="mock")],
        created_at=utc_now(),
    )
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Query customer A",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.SUBTASK_EXECUTION,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    subtask = SubTask(
        id="subtask_1",
        title="Query CRM",
        description="Query customer_id customer_a",
        assigned_agent_id=agent.id,
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: '{"tool_calls": [{"tool_name": "crm_query", "arguments": {"customer_id": "customer_a"}}], "output": ""}',
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert output == ""
    assert tool_calls[0].tool_name == "crm_query"
    assert tool_calls[0].arguments == {"customer_id": "customer_a"}


def test_model_subtask_execution_uses_tool_results(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        tools=[AgentTool(name="crm_query", type="mock")],
        created_at=utc_now(),
    )
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Query customer A",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.SUBTASK_EXECUTION,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    subtask = SubTask(
        id="subtask_1",
        title="Query CRM",
        description="Query customer_id customer_a",
        assigned_agent_id=agent.id,
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: '{"tool_calls": [], "output": "Customer A is VIP"}',
    )

    tool_calls, output = execute_subtask_with_tools_model(
        task,
        subtask,
        agent,
        [
            ToolExecutionResult(
                tool_name="crm_query",
                arguments={"customer_id": "customer_a"},
                success=True,
                result='{"level": "vip"}',
            )
        ],
    )

    assert tool_calls == []
    assert output == "Customer A is VIP"
