import json
import traceback
from pathlib import Path
from urllib import error

import pytest
import yaml
from pydantic import ValidationError

import app.core.model_client as model_client
from app.core.model_client import (
    AgentModelExecutionError,
    ModelCallError,
    OpenAIResponsesClient,
    dispatch_with_model,
    execute_agent_with_model,
    execute_subtask_with_tools_model,
    judge_completion_with_model,
    plan_next_round_with_model,
    recognize_task_with_model,
    recognize_tasks_with_model,
)
from app.core.models import (
    Agent,
    AgentExecutionConfig,
    AgentTool,
    Artifact,
    MAX_AGENT_MODEL_RETRIES,
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskDraft,
    TaskRound,
    ToolExecutionResult,
    utc_now,
)
from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CriterionResultStatus,
    CurrentNode,
    SourceType,
    TaskStatus,
)
from app.services.completion_service import CompletionService


def test_default_model_output_token_budget_is_1024000() -> None:
    assert getattr(model_client, "DEFAULT_MAX_OUTPUT_TOKENS", None) == 1_024_000


def test_docker_compose_model_output_token_budget_is_1024000() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    configured = compose["services"]["multi-agent-pyserver"]["environment"]

    assert configured["MODEL_MAX_OUTPUT_TOKENS"] == "${MODEL_MAX_OUTPUT_TOKENS:-1024000}"


def test_docker_compose_persists_managed_agent_outputs() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    volumes = compose["services"]["multi-agent-pyserver"]["volumes"]

    assert "./runtime/agent_outputs:/app/runtime/agent_outputs" in volumes


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


def test_responses_client_extracts_chat_completion_content_parts() -> None:
    client = OpenAIResponsesClient()

    text = client.extract_text(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": '{"ok":'},
                            {"type": "text", "text": " true}"},
                        ],
                    }
                }
            ]
        }
    )

    assert text == '{"ok": true}'


def test_responses_client_rejects_truncated_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"tool_calls": []'},
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr("app.core.model_client.request.urlopen", lambda req, timeout: FakeResponse())
    client = OpenAIResponsesClient(url="http://model.test/v1/chat/completions", model="test-model")

    with pytest.raises(ModelCallError, match="truncated"):
        client.create("system", "user")


def test_responses_client_rejects_truncated_later_chat_completion_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"tool_calls": [], "output": "complete"}'},
                        },
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"tool_calls": []'},
                        },
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr("app.core.model_client.request.urlopen", lambda req, timeout: FakeResponse())
    client = OpenAIResponsesClient(url="http://model.test/v1/chat/completions", model="test-model")

    with pytest.raises(ModelCallError, match="truncated"):
        client.create("system", "user")


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


def test_model_intent_parses_visible_contract_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        return json.dumps(
            {
                "tasks": [
                    {
                        "title": "Create implementation plan",
                        "description": "Prepare an implementation plan for review",
                        "confidence": 0.92,
                        "goal": "Agree on the implementation approach",
                        "deliverable_goal": "Deliver a reviewable plan",
                        "deliverable_kind": "file",
                        "deliverable_format": "markdown",
                        "deliverable_filename": " implementation-plan.md ",
                        "deliverable_requirements": ["Markdown document", "Include milestones"],
                        "success_criteria": ["Reviewers can make an approval decision"],
                        "requires_human_acceptance": True,
                    }
                ]
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    draft = recognize_tasks_with_model("Create an implementation plan", [])[0]

    assert draft.goal == "Agree on the implementation approach"
    assert draft.deliverable_goal == "Deliver a reviewable plan"
    assert draft.deliverable_kind == "text"
    assert draft.deliverable_format is None
    assert draft.deliverable_filename == ""
    assert draft.deliverable_requirements == []
    assert draft.success_criteria == [
        "Markdown document",
        "Include milestones",
        "Reviewers can make an approval decision",
    ]
    assert draft.requires_human_acceptance is False
    assert '"deliverable_kind"' not in captured["system_prompt"]
    assert '"deliverable_format"' not in captured["system_prompt"]
    assert '"deliverable_filename"' not in captured["system_prompt"]
    assert '"deliverable_requirements"' not in captured["system_prompt"]
    assert '"requires_human_acceptance"' not in captured["system_prompt"]
    assert "1到4条统一验收标准" in captured["system_prompt"]


def test_model_intent_ignores_legacy_delivery_fields_and_merges_acceptance_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        return json.dumps(
            {
                "tasks": [
                    {
                        "title": "Create implementation plan",
                        "description": "Prepare an implementation plan for review",
                        "confidence": 0.92,
                        "goal": "Agree on the implementation approach",
                        "deliverable_goal": "Deliver a reviewable plan",
                        "deliverable_kind": "file",
                        "deliverable_format": "text",
                        "deliverable_filename": "report.patch",
                        "deliverable_requirements": [
                            f"Legacy requirement {index}" for index in range(1, 7)
                        ],
                        "success_criteria": [
                            "Legacy requirement 1",
                            *[f"Visible criterion {index}" for index in range(1, 7)],
                        ],
                        "requires_human_acceptance": True,
                    }
                ]
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    draft = recognize_tasks_with_model("Create an implementation plan", [])[0]

    assert draft.deliverable_kind == "text"
    assert draft.deliverable_format is None
    assert draft.deliverable_filename == ""
    assert draft.deliverable_requirements == []
    assert draft.success_criteria == [
        *[f"Legacy requirement {index}" for index in range(1, 5)],
    ]
    assert draft.requires_human_acceptance is False
    assert '"success_criteria"' in captured["system_prompt"]
    assert '"deliverable_kind"' not in captured["system_prompt"]
    assert '"deliverable_format"' not in captured["system_prompt"]
    assert '"deliverable_filename"' not in captured["system_prompt"]
    assert '"deliverable_requirements"' not in captured["system_prompt"]
    assert '"requires_human_acceptance"' not in captured["system_prompt"]


def test_model_intent_limits_generated_acceptance_criteria_to_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {
                "tasks": [
                    {
                        "title": "验收标准限制",
                        "description": "验证模型验收标准数量限制",
                        "success_criteria": [f"criterion-{index}" for index in range(1, 13)],
                    }
                ]
            }
        ),
    )

    draft = recognize_tasks_with_model("验证验收标准数量", [])[0]

    assert draft.success_criteria == [f"criterion-{index}" for index in range(1, 5)]


@pytest.mark.parametrize(
    (
        "raw_kind",
        "raw_format",
        "raw_filename",
    ),
    [
        ("FILE", "markdown", " report.md "),
        (" file ", "text", " report.txt "),
        ("file", "Markdown", " report.md "),
        ("file", None, 123),
        ("file", {}, " report.md "),
        ("text", "markdown", " report.md "),
        (None, "text", " report.txt "),
        ([], "markdown", " report.md "),
    ],
)
def test_model_intent_ignores_suggested_delivery_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_kind: object,
    raw_format: object,
    raw_filename: object,
) -> None:
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "tasks": [
                    {
                        "title": "Create report",
                        "description": "Prepare a report",
                        "confidence": 0.9,
                        "deliverable_kind": raw_kind,
                        "deliverable_format": raw_format,
                        "deliverable_filename": raw_filename,
                    }
                ]
            }
        ),
    )

    draft = recognize_tasks_with_model("Create report", [])[0]

    assert draft.deliverable_kind == "text"
    assert draft.deliverable_format is None
    assert draft.deliverable_filename == ""


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


def test_round_planner_receives_draft_checklist_and_human_confirmation_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="生成方案后必须由管理员确认，通过后再报价。",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        draft=TaskDraft(
            title="生成技术方案; 管理员确认方案可行性; 整理报价建议",
            description="- 生成技术方案: 基于上下文生成方案。\n- 管理员确认方案可行性: 人工节点。\n- 整理报价建议: 确认后继续。",
            confidence=0.9,
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return '{"should_continue": false, "final_output": "done", "subtasks": []}'

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    plan_next_round_with_model(task, [])

    payload = json.loads(captured["user_prompt"])
    assert payload["task"]["draft"]["title"] == "生成技术方案; 管理员确认方案可行性; 整理报价建议"
    assert "管理员确认方案可行性" in payload["task"]["draft"]["description"]
    assert "draft 任务清单" in captured["system_prompt"]
    assert "人工确认" in captured["system_prompt"]


def _file_delivery_contract() -> TaskContract:
    return TaskContract(
        goal="Create a complete report",
        deliverable_goal="Deliver a reviewable report file",
        deliverable_kind="file",
        deliverable_format="markdown",
        deliverable_filename="report.md",
        success_criteria=[
            TaskContractItem(id="criterion_complete", description="The report is complete")
        ],
        confirmed_at=utc_now(),
    )


def test_legacy_round_planner_includes_contract_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    task = Task(
        id="task_contract",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create a report file",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=_file_delivery_contract(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["user_prompt"] = user_prompt
        return '{"should_continue": false, "final_output": "done", "subtasks": []}'

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    plan_next_round_with_model(task, [])

    payload = json.loads(captured["user_prompt"])
    assert payload["task"]["contract"] == task.contract.model_dump(mode="json")


def test_legacy_round_planner_does_not_send_tool_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_value = "planner-tool-secret-placeholder"
    agent = Agent(
        id="agent_secure_planner",
        name="Secure Planner Agent",
        capabilities=["planning"],
        tools=[
            AgentTool(
                name="customer_query",
                description="Query customer records",
                type="mysql",
                config={"password": sensitive_value, "host": "private-db"},
                input_schema={"type": "object", "properties": {"customer_id": {"type": "string"}}},
            )
        ],
        created_at=utc_now(),
    )
    task = Task(
        id="task_secure_planner",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Plan a customer report",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    captured = {}

    def fake_create(_system_prompt: str, user_prompt: str) -> str:
        captured["user_prompt"] = user_prompt
        return '{"should_continue": false, "final_output": "done", "subtasks": []}'

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    plan_next_round_with_model(task, [agent])

    payload = json.loads(captured["user_prompt"])
    tool_payload = payload["available_agents"][0]["tools"][0]
    assert tool_payload == {
        "name": "customer_query",
        "description": "Query customer records",
        "type": "mysql",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
        },
    }
    assert sensitive_value not in captured["user_prompt"]
    assert "config" not in tool_payload


def test_completion_judge_receives_draft_checklist_and_cannot_skip_human_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    task = Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="生成方案后必须由管理员确认，通过后再报价。",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        draft=TaskDraft(
            title="生成技术方案; 管理员确认方案可行性; 整理报价建议",
            description="- 生成技术方案\n- 管理员确认方案可行性\n- 整理报价建议",
            confidence=0.9,
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return '{"complete": false}'

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    assert judge_completion_with_model(task, "只查询了客户需求") is False

    payload = json.loads(captured["user_prompt"])
    assert payload["task"]["draft"]["title"] == "生成技术方案; 管理员确认方案可行性; 整理报价建议"
    assert "管理员确认方案可行性" in payload["task"]["draft"]["description"]
    assert "未完成人工确认" in captured["system_prompt"]


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


def test_model_execution_payloads_do_not_send_tool_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_value = "execution-tool-secret-placeholder"
    agent = Agent(
        id="agent_secure_execution",
        name="Secure Execution Agent",
        description="Uses a credentialed lookup tool",
        capabilities=["lookup"],
        execution_config={"system_prompt": "Follow the approved lookup procedure."},
        metadata={"private_note": sensitive_value},
        tools=[
            AgentTool(
                name="secure_lookup",
                description="Lookup a record",
                type="http",
                config={"authorization": sensitive_value, "url": "https://private.example"},
                input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            )
        ],
        created_at=utc_now(),
    )
    task = Task(
        id="task_secure_execution",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Lookup record A",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.SUBTASK_EXECUTION,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    subtask = SubTask(
        id="subtask_secure_execution",
        title="Lookup record",
        description="Lookup record A",
        assigned_agent_id=agent.id,
    )
    captured_prompts = []

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured_prompts.append((system_prompt, user_prompt))
        if len(captured_prompts) == 1:
            return "lookup complete"
        return '{"tool_calls": [], "output": "lookup complete"}'

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    assert execute_agent_with_model(task, agent) == "lookup complete"
    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == "lookup complete"
    for system_prompt, rendered_prompt in captured_prompts:
        payload = json.loads(rendered_prompt)
        tool_payload = payload["agent"]["tools"][0]
        assert tool_payload == {
            "name": "secure_lookup",
            "description": "Lookup a record",
            "type": "http",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        }
        assert sensitive_value not in rendered_prompt
        assert "config" not in tool_payload
        assert "metadata" not in payload["agent"]
        assert "execution_config" not in payload["agent"]
        assert "Follow the approved lookup procedure." in system_prompt


def test_model_tool_projection_infers_safe_placeholder_schema_without_exposing_template() -> None:
    sensitive_value = "database-password-placeholder"
    tool = AgentTool(
        name="customer_query",
        description="Query a configured customer",
        type="mysql",
        config={
            "password": sensitive_value,
            "query": "SELECT * FROM customers WHERE customer_id = '{customer_id}' AND region = '{region}'",
        },
    )

    payload = model_client.model_tool_payload(tool)

    assert payload == {
        "name": "customer_query",
        "description": "Query a configured customer",
        "type": "mysql",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["customer_id", "region"],
        },
    }
    assert sensitive_value not in json.dumps(payload)
    assert "SELECT" not in json.dumps(payload)


def test_model_agent_projection_is_a_deep_copy() -> None:
    agent = Agent(
        id="agent_projection_copy",
        name="Projection Agent",
        capabilities=["lookup"],
        input_schema={"properties": {"request": {"type": "string"}}},
        output_schema={"properties": {"result": {"type": "string"}}},
        tools=[
            AgentTool(
                name="lookup",
                type="http",
                input_schema={"properties": {"id": {"type": "string"}}},
            )
        ],
        created_at=utc_now(),
    )

    payload = model_client.model_agent_payload(agent)
    payload["capabilities"].append("mutated")
    payload["input_schema"]["properties"]["request"]["type"] = "number"
    payload["tools"][0]["input_schema"]["properties"]["id"]["type"] = "number"

    assert agent.capabilities == ["lookup"]
    assert agent.input_schema["properties"]["request"]["type"] == "string"
    assert agent.tools[0].input_schema["properties"]["id"]["type"] == "string"


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


def _subtask_execution_context(
    max_retries: int,
    *,
    contract: TaskContract | None = None,
    tools: list[AgentTool] | None = None,
) -> tuple[Task, SubTask, Agent]:
    agent = Agent(
        id="agent_report",
        name="Report Agent",
        description="Creates reports",
        capabilities=["report"],
        execution_config={"max_retries": max_retries},
        tools=list(tools or []),
        created_at=utc_now(),
    )
    task = Task(
        id="task_report",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create a report",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.SUBTASK_EXECUTION,
        contract=contract,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    subtask = SubTask(
        id="subtask_report",
        title="Create report",
        description="Create the final report",
        assigned_agent_id=agent.id,
    )
    return task, subtask, agent


def test_file_delivery_with_only_file_write_exposes_tool_and_keeps_config_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_value = "hidden-file-tool-placeholder"
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[
            AgentTool(
                name="write_delivery",
                description="Write the final delivery file",
                type="file_write",
                config={"credential": sensitive_value},
            )
        ],
    )
    body = "# Customer report\n\n## Summary\n\nThe complete reviewable result."
    captured = {}

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return body

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    payload = json.loads(captured["user_prompt"])
    assert tool_calls == []
    assert output == body
    assert payload["main_task"]["contract"] == task.contract.model_dump(mode="json")
    assert payload["agent"]["tools"] == [
        {
            "name": "write_delivery",
            "description": "Write the final delivery file",
            "type": "file_write",
            "input_schema": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filename", "content"],
            },
        }
    ]
    assert "file_write" in captured["system_prompt"]
    assert "必须调用该工具真实写入文件" in captured["system_prompt"]
    assert "系统托管目录兜底" in captured["system_prompt"]
    assert "只返回简短可读文本" not in captured["system_prompt"]
    assert "不要返回 Markdown" not in captured["system_prompt"]
    assert sensitive_value not in captured["system_prompt"]
    assert sensitive_value not in captured["user_prompt"]


def test_file_delivery_lookup_then_plain_markdown_body_uses_visible_auxiliary_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[
            AgentTool(name="customer_lookup", type="mock"),
            AgentTool(name="write_delivery", type="file_write"),
        ],
    )
    body = "# Customer report\n\nCustomer A is ready for renewal."
    responses = iter(
        [
            json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool_name": "customer_lookup",
                            "arguments": {"customer_id": "customer_a"},
                        }
                    ],
                    "output": "",
                }
            ),
            body,
        ]
    )
    payloads = []

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        payloads.append(json.loads(user_prompt))
        return next(responses)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])
    followup_calls, followup_output = execute_subtask_with_tools_model(
        task,
        subtask,
        agent,
        [
            ToolExecutionResult(
                tool_name="customer_lookup",
                arguments={"customer_id": "customer_a"},
                success=True,
                result='{"status": "ready"}',
            )
        ],
    )

    assert [call.tool_name for call in tool_calls] == ["customer_lookup"]
    assert output == ""
    assert followup_calls == []
    assert followup_output == body
    assert [tool["name"] for tool in payloads[0]["agent"]["tools"]] == [
        "customer_lookup",
        "write_delivery",
    ]
    assert [tool["name"] for tool in payloads[1]["agent"]["tools"]] == [
        "customer_lookup",
        "write_delivery",
    ]
    assert payloads[1]["tool_results"][0]["tool_name"] == "customer_lookup"


def test_file_delivery_duplicate_name_keeps_first_file_write_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=1,
        contract=_file_delivery_contract(),
        tools=[
            AgentTool(name="shared_tool", type="file_write"),
            AgentTool(name="shared_tool", type="mock", config={"response": "mock result"}),
        ],
    )
    body = "# Final report\n\nReturned without executing the hidden file writer."
    responses = iter(
        [
            json.dumps(
                {
                    "tool_calls": [
                        {"tool_name": "shared_tool", "arguments": {"content": "wrong"}}
                    ],
                    "output": "",
                }
            ),
            body,
        ]
    )
    payloads = []

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        payloads.append(json.loads(user_prompt))
        return next(responses)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert len(payloads) == 1
    assert payloads[0]["agent"]["tools"][0]["type"] == "file_write"
    assert [call.tool_name for call in tool_calls] == ["shared_tool"]
    assert output == ""


def test_file_delivery_duplicate_name_first_mock_is_only_visible_tool_and_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[
            AgentTool(name="shared_tool", type="mock", config={"response": "mock result"}),
            AgentTool(name="shared_tool", type="file_write"),
        ],
    )
    captured = {}

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["payload"] = json.loads(user_prompt)
        return json.dumps(
            {
                "tool_calls": [{"tool_name": "shared_tool", "arguments": {"query": "A"}}],
                "output": "",
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert captured["payload"]["agent"]["tools"] == [
        {
            "name": "shared_tool",
            "description": "",
            "type": "mock",
            "input_schema": {},
        }
    ]
    assert "config" not in captured["payload"]["agent"]["tools"][0]
    assert [call.tool_name for call in tool_calls] == ["shared_tool"]
    assert output == ""


@pytest.mark.parametrize("invalid_tool_name", ["file_write", "unknown_tool"])
def test_file_delivery_visible_tools_retry_non_whitelisted_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    invalid_tool_name: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=1,
        contract=_file_delivery_contract(),
        tools=[
            AgentTool(name="customer_lookup", type="mock"),
            AgentTool(name="write_delivery", type="file_write"),
        ],
    )
    body = "# Final report\n\nNo unregistered tool was executed."
    responses = iter(
        [
            json.dumps(
                {
                    "tool_calls": [
                        {"tool_name": invalid_tool_name, "arguments": {"content": "wrong"}}
                    ],
                    "output": "",
                }
            ),
            body,
        ]
    )
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        attempts += 1
        return next(responses)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 2
    assert tool_calls == []
    assert output == body


@pytest.mark.parametrize(
    "hallucinated_response",
    [
        '```json\n{"tool_calls": [{"tool_name": "write_delivery", "arguments": {"content": "wrong channel"}}',
        '```JSON\n{"tool_calls": [{"tool_name": "write_delivery", "arguments": {"content": "wrong channel"}}',
        '{"output": "", "tool_calls": [{"tool_name": "write_delivery", "arguments": {"content": "wrong channel"}}]',
        '```json\n{"output": "", "tool_calls": [{"tool_name": "write_delivery", "arguments": {"content": "wrong channel"}}]',
        "{'output': '', 'tool_calls': [{'tool_name': 'write_delivery', 'arguments': {'content': 'wrong channel'}}]}",
    ],
)
def test_file_delivery_retries_valid_or_malformed_file_write_hallucination(
    monkeypatch: pytest.MonkeyPatch,
    hallucinated_response: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=1,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    body = "# Final report\n\nReturned through the managed delivery channel."
    responses = iter([hallucinated_response, body])
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        attempts += 1
        return next(responses)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 2
    assert tool_calls == []
    assert output == body


def test_file_delivery_accepts_registered_file_write_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: json.dumps(
            {
                "tool_calls": [
                    {
                        "tool_name": "write_delivery",
                        "arguments": {
                            "filename": "delivery.md",
                            "content": "# Final report",
                        },
                    }
                ],
                "output": "",
            }
        ),
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert output == ""
    assert [call.tool_name for call in tool_calls] == ["write_delivery"]
    assert tool_calls[0].arguments["filename"] == "delivery.md"


def test_file_delivery_accepts_plain_body_with_embedded_tool_calls_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    body = (
        "# Protocol guide\n\n"
        "Example only:\n\n"
        '```json\n{"output": "", "tool_calls": [{"tool_name": "write_delivery"}]}\n```\n\n'
        "This JSON is documentation, not a tool request."
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: body,
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == body


def test_file_delivery_accepts_closed_leading_json_example_followed_by_markdown_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    body = (
        '```json\n{"output": "", "tool_calls": '
        '[{"tool_name": "write_delivery", "arguments": {"content": "wrong channel"}}]}\n'
        "```\n\n"
        "这是文档说明"
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: body,
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == body


def test_file_delivery_accepts_complete_json_object_followed_by_markdown_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    body = '{"tool_calls": [], "output": ""}\n\n# Protocol documentation'
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: body,
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == body


def test_file_delivery_change_keeps_text_delivery_json_tool_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    captured = {}

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return json.dumps(
            {
                "tool_calls": [
                    {
                        "tool_name": "write_delivery",
                        "arguments": {"filename": "report.md", "content": "report"},
                    }
                ],
                "output": "",
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    payload = json.loads(captured["user_prompt"])
    assert [call.tool_name for call in tool_calls] == ["write_delivery"]
    assert output == ""
    assert [tool["name"] for tool in payload["agent"]["tools"]] == ["write_delivery"]
    assert '返回 JSON 格式: {"tool_calls"' in captured["system_prompt"]
    assert "只返回简短可读文本，不要返回 Markdown" in captured["system_prompt"]


def test_model_subtask_execution_retries_http_and_model_call_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=2)
    responses: list[Exception | str] = [
        error.HTTPError("http://model.test", 503, "unavailable", None, None),
        ModelCallError("temporary model failure"),
        '{"tool_calls": [], "output": "report ready"}',
    ]
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        response = responses[attempts]
        attempts += 1
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 3
    assert tool_calls == []
    assert output == "report ready"


def test_model_subtask_execution_retries_empty_and_json_like_protocol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=2)
    responses = [
        "   ",
        '{"tool_calls": [], "output": "incomplete"',
        '{"tool_calls": [], "output": "report ready"}',
    ]
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        response = responses[attempts]
        attempts += 1
        return response

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 3
    assert tool_calls == []
    assert output == "report ready"


def test_file_delivery_repairs_malformed_tool_call_json_with_default_retry_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=0,
        contract=_file_delivery_contract(),
        tools=[AgentTool(name="write_delivery", type="file_write")],
    )
    responses = [
        '{"tool_calls": [{"tool_name": "write_delivery", "arguments": '
        '{"filename": "report.md", "content": "# Report"}}] "output": ""}',
        json.dumps(
            {
                "tool_calls": [
                    {
                        "tool_name": "write_delivery",
                        "arguments": {
                            "filename": "report.md",
                            "content": "# Report\n\nComplete body",
                        },
                    }
                ],
                "output": "",
            }
        ),
    ]
    prompts: list[str] = []

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        prompts.append(user_prompt)
        return responses[len(prompts) - 1]

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert len(prompts) == 2
    assert output == ""
    assert [call.tool_name for call in tool_calls] == ["write_delivery"]
    repair_payload = json.loads(prompts[1])
    assert "上一次响应无法按要求解析" in repair_payload["instruction"]
    assert "Expecting ',' delimiter" in repair_payload["parse_error"]
    assert repair_payload["invalid_response"] == responses[0]


def test_model_subtask_execution_accepts_non_json_text_as_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=1)
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: "Customer A is VIP and ready for renewal.",
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == "Customer A is VIP and ready for renewal."


def test_model_subtask_execution_accepts_plain_text_with_placeholder_braces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=1)
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: "Use {customer_name} in the final report.",
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == "Use {customer_name} in the final report."


@pytest.mark.parametrize(
    "response",
    [
        'Report includes {"status":"ok"} as an example.',
        '[DONE] report ready',
        '"report ready"',
        '```json\n"report ready"\n```',
        '```json\n["report ready"]\n```',
        (
            'Report includes {"tool_calls":[{"tool_name":"crm_query","arguments":{}}],'
            '"output":""} as an example.'
        ),
    ],
)
def test_model_subtask_execution_accepts_text_outside_json_object_envelope(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: response,
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == response


def test_model_subtask_execution_parses_json_fenced_object_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: '```json\n{"tool_calls": [], "output": "report ready"}\n```',
    )

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert tool_calls == []
    assert output == "report ready"


@pytest.mark.parametrize(
    "response",
    [
        '{"tool_calls": {}, "output": "report ready"}',
        '{"tool_calls": [], "output": null}',
        '{"tool_calls": [], "output": "report ready"} trailing text',
    ],
)
def test_model_subtask_execution_rejects_invalid_json_protocol_fields(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: response,
    )

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    assert caught.value.attempts == 1
    assert caught.value.last_error != "None"


@pytest.mark.parametrize(
    "tool_call",
    [
        None,
        {"tool_name": 123, "arguments": {}},
        {"tool_name": "", "arguments": {}},
        {"tool_name": "crm_query"},
        {"tool_name": "crm_query", "arguments": []},
    ],
)
def test_model_subtask_execution_rejects_invalid_tool_call_items(
    monkeypatch: pytest.MonkeyPatch,
    tool_call: object,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)
    response = json.dumps({"tool_calls": [tool_call], "output": "report ready"})
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda system_prompt, user_prompt: response,
    )

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    assert caught.value.attempts == 1


def test_model_subtask_execution_retries_invalid_tool_call_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=1)
    responses = [
        '{"tool_calls": [{"tool_name": "crm_query", "arguments": []}], "output": ""}',
        '{"tool_calls": [], "output": "report ready"}',
    ]
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        response = responses[attempts]
        attempts += 1
        return response

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 2
    assert tool_calls == []
    assert output == "report ready"


@pytest.mark.parametrize(
    ("error_message", "sensitive_value"),
    [
        ("Authorization: Bearer test-secret-model-token", "test-secret-model-token"),
        ("Incorrect API key provided: sk-test-model-token", "sk-test-model-token"),
        ("Incorrect API key provided: placeholder-sensitive-value", "placeholder-sensitive-value"),
        ("access token: placeholder-access-value", "placeholder-access-value"),
        ("refresh token=placeholder-refresh-value", "placeholder-refresh-value"),
    ],
)
def test_model_subtask_execution_raises_sanitized_error_after_retries(
    monkeypatch: pytest.MonkeyPatch,
    error_message: str,
    sensitive_value: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=2)
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        attempts += 1
        raise ModelCallError(error_message)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == 3
    assert caught.value.attempts == 3
    assert sensitive_value not in caught.value.last_error
    assert sensitive_value not in str(caught.value)
    assert sensitive_value not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    ("error_message", "sensitive_words", "retained_text"),
    [
        (
            "password: correct horse battery staple\nretry failed",
            ("correct", "horse", "battery", "staple"),
            "retry failed",
        ),
        (
            "secret=violet indigo crimson; status=invalid",
            ("violet", "indigo", "crimson"),
            "status=invalid",
        ),
        (
            "token: alpha bravo charlie, request rejected",
            ("alpha", "bravo", "charlie"),
            "request rejected",
        ),
        (
            "API key provided: north south east west\nquota exceeded",
            ("north", "south", "east", "west"),
            "quota exceeded",
        ),
        (
            "access token: spring summer autumn winter\nrequest rejected",
            ("spring", "summer", "autumn", "winter"),
            "request rejected",
        ),
    ],
)
def test_model_subtask_execution_redacts_unquoted_multiword_secrets_on_one_line(
    monkeypatch: pytest.MonkeyPatch,
    error_message: str,
    sensitive_words: tuple[str, ...],
    retained_text: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        raise ModelCallError(error_message)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    rendered_errors = (
        caught.value.last_error,
        str(caught.value),
        "".join(traceback.format_exception(caught.value)),
    )
    for rendered_error in rendered_errors:
        assert retained_text in rendered_error
        for sensitive_word in sensitive_words:
            assert sensitive_word not in rendered_error


@pytest.mark.parametrize(
    ("error_message", "sensitive_value", "retained_text"),
    [
        (
            "API key placeholder-sensitive-value is invalid",
            "placeholder-sensitive-value",
            "is invalid",
        ),
        (
            "token placeholder-token-value expired",
            "placeholder-token-value",
            "expired",
        ),
        (
            "credential=placeholder-credential-value, request rejected",
            "placeholder-credential-value",
            "request rejected",
        ),
        (
            "API key is placeholder-api-value",
            "placeholder-api-value",
            "API key is",
        ),
        (
            "API key provided placeholder-api-value",
            "placeholder-api-value",
            "API key provided",
        ),
        (
            "API key provided is placeholder-secret-value",
            "placeholder-secret-value",
            "API key provided is",
        ),
        (
            "access token is placeholder-access-value",
            "placeholder-access-value",
            "access token is",
        ),
        (
            "client secret provided placeholder-client-value",
            "placeholder-client-value",
            "client secret provided",
        ),
        (
            "Authorization Basic placeholder-basic-value",
            "placeholder-basic-value",
            "Basic",
        ),
    ],
)
def test_model_subtask_execution_redacts_secret_formats_without_colon(
    monkeypatch: pytest.MonkeyPatch,
    error_message: str,
    sensitive_value: str,
    retained_text: str,
) -> None:
    task, subtask, agent = _subtask_execution_context(max_retries=0)

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        raise ModelCallError(error_message)

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    rendered_error = "".join(traceback.format_exception(caught.value))
    assert sensitive_value not in caught.value.last_error
    assert sensitive_value not in str(caught.value)
    assert sensitive_value not in rendered_error
    assert retained_text in caught.value.last_error


@pytest.mark.parametrize(
    "diagnostic",
    [
        "token limit exceeded",
        "token count mismatch",
        "token expired",
    ],
)
def test_error_sanitization_preserves_non_secret_token_diagnostics(
    diagnostic: str,
) -> None:
    assert model_client._sanitize_error_text(diagnostic) == diagnostic


@pytest.mark.parametrize("max_retries", [-1, MAX_AGENT_MODEL_RETRIES + 1])
def test_agent_execution_config_rejects_retry_values_outside_bounds(
    max_retries: int,
) -> None:
    with pytest.raises(ValidationError):
        AgentExecutionConfig(max_retries=max_retries)


def test_model_subtask_execution_caps_mutated_retry_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, subtask, agent = _subtask_execution_context(
        max_retries=MAX_AGENT_MODEL_RETRIES,
    )
    agent.execution_config.max_retries = MAX_AGENT_MODEL_RETRIES + 100
    attempts = 0

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        nonlocal attempts
        attempts += 1
        raise ModelCallError("temporary model failure")

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    with pytest.raises(AgentModelExecutionError) as caught:
        execute_subtask_with_tools_model(task, subtask, agent, [])

    assert attempts == MAX_AGENT_MODEL_RETRIES + 1
    assert caught.value.attempts == MAX_AGENT_MODEL_RETRIES + 1


def test_model_evaluates_each_success_criterion_with_structured_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate = getattr(model_client, "evaluate_success_criteria_with_model", None)
    assert evaluate is not None
    task = Task(
        id="task_criteria",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare a delivery plan",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=TaskContract(
            goal="Prepare plan",
            deliverable_goal="Reviewable plan",
            success_criteria=[TaskContractItem(id="criterion_reviewable", description="Plan is reviewable")],
            confirmed_at=utc_now(),
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: (
            '{"criterion_results": [{"criterion_id": "criterion_reviewable", "status": "passed", '
            '"evidence_text": "Delivery plan contains review sections", "reason": "Criterion is satisfied"}]}'
        ),
    )

    results = evaluate(task, "Delivery plan contains review sections")

    assert results is not None
    assert results[0].criterion_id == "criterion_reviewable"
    assert results[0].status == CriterionResultStatus.PASSED
    assert results[0].evidence_text == "Delivery plan contains review sections"


def test_model_success_criteria_evaluation_receives_complete_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_two_success_criteria()
    task.active_execution_id = "execution_1"
    task.context.summary = "已汇总技术方案和人工审核意见"
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="subtask_human_review",
                    title="人工审核",
                    description="审核方案",
                    assignee_type="human",
                    status=TaskStatus.SUCCEEDED,
                    output="王大锤确认方案可执行",
                    result_metadata={"decision": "approved", "comment": "同意上线"},
                    tool_results=[
                        ToolExecutionResult(
                            tool_name="review_record",
                            tool_type="mock",
                            success=True,
                            result="审核记录已保存",
                        )
                    ],
                )
            ],
        )
    ]
    task.artifacts = [
        Artifact(
            id="artifact_plan",
            task_id=task.id,
            execution_id="execution_1",
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.TASK_RESULT,
            source_id=task.id,
            name="技术方案",
            content="完整技术方案内容",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=utc_now(),
        )
    ]
    captured: dict[str, str] = {}

    def fake_create(_system_prompt: str, user_prompt: str) -> str:
        captured["user_prompt"] = user_prompt
        return json.dumps(
            {
                "criterion_results": [
                    {"criterion_id": "criterion_reviewable", "status": "passed"},
                    {"criterion_id": "criterion_complete", "status": "passed"},
                ]
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    model_client.evaluate_success_criteria_with_model(task, "最终交付结果")

    evidence = json.loads(captured["user_prompt"])["execution_evidence"]
    assert evidence["final_output"] == "最终交付结果"
    assert evidence["context_summary"] == "已汇总技术方案和人工审核意见"
    assert evidence["node_outputs"][0]["output"] == "王大锤确认方案可执行"
    assert evidence["node_outputs"][0]["result_metadata"]["comment"] == "同意上线"
    assert evidence["node_outputs"][0]["tool_results"][0]["result"] == "审核记录已保存"
    assert evidence["valid_artifacts"][0]["artifact_id"] == "artifact_plan"


def _task_with_two_success_criteria() -> Task:
    return Task(
        id="task_two_criteria",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare a delivery plan",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=TaskContract(
            goal="Prepare plan",
            deliverable_goal="Reviewable plan",
            success_criteria=[
                TaskContractItem(id="criterion_reviewable", description="Plan is reviewable"),
                TaskContractItem(id="criterion_complete", description="Plan is complete"),
            ],
            confirmed_at=utc_now(),
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
    )


@pytest.mark.parametrize(
    "duplicate_statuses",
    [
        ("passed", "failed"),
        ("failed", "passed"),
        ("passed", "passed"),
    ],
)
def test_model_criterion_evaluation_rejects_duplicate_known_ids(
    monkeypatch: pytest.MonkeyPatch,
    duplicate_statuses: tuple[str, str],
) -> None:
    task = _task_with_two_success_criteria()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {
                "criterion_results": [
                    {"criterion_id": "criterion_reviewable", "status": duplicate_statuses[0]},
                    {"criterion_id": "criterion_reviewable", "status": duplicate_statuses[1]},
                    {"criterion_id": "criterion_complete", "status": "passed"},
                ]
            }
        ),
    )

    results = model_client.evaluate_success_criteria_with_model(task, "Complete reviewable plan")

    assert results is not None
    assert [result.criterion_id for result in results] == ["criterion_reviewable", "criterion_complete"]
    assert [result.status for result in results] == [
        CriterionResultStatus.PENDING,
        CriterionResultStatus.PENDING,
    ]
    assert all("duplicate" in result.reason.lower() for result in results)


def test_model_criterion_evaluation_marks_missing_legal_id_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_two_success_criteria()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: (
            '{"criterion_results": [{"criterion_id": "criterion_reviewable", "status": "passed"}]}'
        ),
    )

    results = model_client.evaluate_success_criteria_with_model(task, "Reviewable but incomplete plan")

    assert results is not None
    assert [result.status for result in results] == [
        CriterionResultStatus.PASSED,
        CriterionResultStatus.PENDING,
    ]
    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Reviewable but incomplete plan",
        reason="Model evaluation completed",
        criterion_results=results,
    )
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True


def test_model_criterion_evaluation_ignores_unknown_id_and_marks_missing_legal_id_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_two_success_criteria()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda _system_prompt, _user_prompt: json.dumps(
            {
                "criterion_results": [
                    {"criterion_id": "criterion_unknown", "status": "passed"},
                    {"criterion_id": "criterion_reviewable", "status": "passed"},
                ]
            }
        ),
    )

    results = model_client.evaluate_success_criteria_with_model(task, "Reviewable but incomplete plan")

    assert results is not None
    assert [result.criterion_id for result in results] == ["criterion_reviewable", "criterion_complete"]
    assert [result.status for result in results] == [
        CriterionResultStatus.PASSED,
        CriterionResultStatus.PENDING,
    ]
    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Reviewable but incomplete plan",
        reason="Model evaluation completed",
        criterion_results=results,
    )
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True


def _task_with_deliverable_requirements() -> tuple[Task, list[Artifact]]:
    now = utc_now()
    task = Task(
        id="task_deliverables",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=TaskContract(
            goal="Prepare delivery",
            deliverable_goal="Delivery package",
            deliverable_requirements=[
                TaskContractItem(id="requirement_pdf", description="Provide PDF"),
                TaskContractItem(id="requirement_summary", description="Provide summary"),
            ],
            success_criteria=[TaskContractItem(id="criterion_1", description="Reviewable")],
            confirmed_at=now,
        ),
        created_at=now,
        updated_at=now,
    )
    artifacts = [
        Artifact(
            id="artifact_pdf",
            task_id=task.id,
            execution_id="execution_1",
            kind=ArtifactKind.FILE,
            source_type=ArtifactSourceType.TOOL_RESULT,
            source_id="tool_execution_pdf",
            name="delivery.pdf",
            uri="file:///tmp/delivery.pdf",
            checksum="sha256:pdf",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=now,
        ),
        Artifact(
            id="artifact_summary",
            task_id=task.id,
            execution_id="execution_1",
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.SUBTASK_OUTPUT,
            source_id="summary",
            name="Summary",
            content="Summary",
            checksum="sha256:summary",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=now,
        ),
    ]
    return task, artifacts


def test_model_deliverable_evaluation_uses_sanitized_artifact_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, artifacts = _task_with_deliverable_requirements()
    sensitive_path = (tmp_path / "private" / "managed-delivery.md").resolve()
    sensitive_value = "customer-secret-token"
    artifact = artifacts[0].model_copy(
        update={
            "name": "managed-delivery.md",
            "content": "Reviewable managed delivery",
            "uri": sensitive_path.as_uri(),
            "media_type": "text/markdown",
            "metadata": {
                "tool_name": "write_delivery",
                "tool_type": "file_write",
                "managed_final_delivery": True,
                "deliverable_format": "markdown",
                "content_length": len("Reviewable managed delivery"),
                "arguments": {"token": sensitive_value},
                "private_note": sensitive_value,
            },
        }
    )
    captured_prompt = ""

    def fake_create(_system_prompt: str, user_prompt: str) -> str:
        nonlocal captured_prompt
        captured_prompt = user_prompt
        return json.dumps(
            {
                "deliverable_results": [
                    {
                        "requirement_id": requirement.id,
                        "status": "passed",
                        "artifact_ids": [artifact.id],
                        "reason": "Selected artifact satisfies the requirement",
                    }
                    for requirement in task.contract.deliverable_requirements
                ]
            }
        )

    monkeypatch.setattr("app.core.model_client.default_client.create", fake_create)

    results = model_client.evaluate_deliverable_requirements_with_model(
        task,
        [artifact],
    )

    assert results is not None
    payload = json.loads(captured_prompt)
    assert payload["selected_artifacts"] == [
        {
            "artifact_id": artifact.id,
            "kind": artifact.kind.value,
            "name": "managed-delivery.md",
            "content": "Reviewable managed delivery",
            "media_type": "text/markdown",
            "metadata": {
                "tool_name": "write_delivery",
                "tool_type": "file_write",
                "managed_final_delivery": True,
                "deliverable_format": "markdown",
                "content_length": len("Reviewable managed delivery"),
            },
        }
    ]
    assert str(sensitive_path) not in captured_prompt
    assert sensitive_path.as_uri() not in captured_prompt
    assert sensitive_value not in captured_prompt
    assert "arguments" not in captured_prompt
    assert "private_note" not in captured_prompt


@pytest.mark.parametrize("statuses", [("passed", "failed"), ("failed", "passed")])
def test_model_deliverable_evaluation_rejects_duplicate_requirement_ids(
    monkeypatch: pytest.MonkeyPatch,
    statuses: tuple[str, str],
) -> None:
    evaluate = getattr(model_client, "evaluate_deliverable_requirements_with_model", None)
    assert evaluate is not None
    task, artifacts = _task_with_deliverable_requirements()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda *_args: json.dumps(
            {
                "deliverable_results": [
                    {"requirement_id": "requirement_pdf", "status": statuses[0], "artifact_ids": ["artifact_pdf"]},
                    {"requirement_id": "requirement_pdf", "status": statuses[1], "artifact_ids": ["artifact_pdf"]},
                    {"requirement_id": "requirement_summary", "status": "passed", "artifact_ids": ["artifact_summary"]},
                ]
            }
        ),
    )

    results = evaluate(task, artifacts)

    assert results is not None
    assert [result.status for result in results] == [
        CriterionResultStatus.PENDING,
        CriterionResultStatus.PENDING,
    ]


@pytest.mark.parametrize("include_unknown", [False, True])
def test_model_deliverable_evaluation_marks_missing_requirement_pending(
    monkeypatch: pytest.MonkeyPatch,
    include_unknown: bool,
) -> None:
    evaluate = getattr(model_client, "evaluate_deliverable_requirements_with_model", None)
    assert evaluate is not None
    task, artifacts = _task_with_deliverable_requirements()
    raw_results = [
        {"requirement_id": "requirement_pdf", "status": "passed", "artifact_ids": ["artifact_pdf"]}
    ]
    if include_unknown:
        raw_results.append(
            {"requirement_id": "requirement_unknown", "status": "passed", "artifact_ids": ["artifact_summary"]}
        )
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda *_args: json.dumps({"deliverable_results": raw_results}),
    )

    results = evaluate(task, artifacts)

    assert results is not None
    assert [result.status for result in results] == [
        CriterionResultStatus.PASSED,
        CriterionResultStatus.PENDING,
    ]


@pytest.mark.parametrize("status", ["passed", "failed", "pending"])
def test_model_deliverable_evaluation_rejects_artifact_outside_selected_set(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    evaluate = getattr(model_client, "evaluate_deliverable_requirements_with_model", None)
    assert evaluate is not None
    task, artifacts = _task_with_deliverable_requirements()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda *_args: json.dumps(
            {
                "deliverable_results": [
                    {
                        "requirement_id": "requirement_pdf",
                        "status": status,
                        "artifact_ids": ["artifact_not_selected"],
                    }
                ]
            }
        ),
    )

    results = evaluate(task, artifacts)

    assert results is not None
    assert results[0].status == CriterionResultStatus.PENDING
    assert results[0].artifact_ids == []


def test_model_deliverable_evaluation_returns_none_when_model_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate = getattr(model_client, "evaluate_deliverable_requirements_with_model", None)
    assert evaluate is not None
    task, artifacts = _task_with_deliverable_requirements()
    monkeypatch.setattr(
        "app.core.model_client.default_client.create",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    assert evaluate(task, artifacts) is None
