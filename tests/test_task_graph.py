from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.model_client import AgentModelExecutionError
from app.core.models import (
    AgentCreate,
    AgentTool,
    RoundPlan,
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    TaskRound,
    ToolCall,
    new_id,
    utc_now,
)
from app.services.storage import AgentRegistry
from app.services.artifact_service import ArtifactService
from app.workflows.task_graph import TaskGraphRunner


def _legacy_contract() -> TaskContract:
    return TaskContract(
        goal="Complete the task",
        deliverable_goal="Reviewable output",
        success_criteria=[TaskContractItem(id="criterion_legacy", description="Output is available")],
        confirmed_at=utc_now(),
        legacy_inferred=True,
    )


def _task_with_active_execution(task_id: str) -> Task:
    now = utc_now()
    contract = _legacy_contract()
    execution = TaskExecution(
        id=f"execution_{task_id}",
        task_id=task_id,
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    )
    return Task(
        id=task_id,
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare artifact delivery",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        title="Prepare artifact delivery",
        description="Prepare artifact delivery",
        contract=contract,
        executions=[execution],
        active_execution_id=execution.id,
        created_at=now,
        updated_at=now,
    )


def _agent_execution_context(
    tmp_path: Path,
    *,
    tools: list[AgentTool] | None = None,
):
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Execution Agent",
            description="Handles model execution tests",
            capabilities=["execution"],
            tools=tools or [],
        )
    )
    task = _task_with_active_execution(new_id("task"))
    subtask = SubTask(
        id="subtask_execution",
        title="Execute model task",
        description="Execute model task",
        assigned_agent_id=agent.id,
    )
    return TaskGraphRunner(registry), task, subtask, agent


def test_task_graph_runner_dispatches_executes_and_closes_task(tmp_path: Path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )
    task = _task_with_active_execution(new_id("task"))
    task.content = "Create quote for customer D"
    task.title = "Create quote for customer D"
    task.description = "Prepare quote for customer D"

    result = TaskGraphRunner(registry).run(task)

    assert result.task_status == TaskStatus.SUCCEEDED
    assert result.current_node == CurrentNode.COMPLETION_JUDGE
    assert result.assigned_agent_id == agent.id
    assert result.loop_count == 1
    assert result.context.summary
    assert [event.type for event in result.events] == [
        "dispatch_decided",
        "agent_executed",
        "context_updated",
        "completion_judged",
        "completion_judged",
    ]


def test_task_graph_stops_at_human_acceptance_without_sealing_execution(
    tmp_path: Path,
) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    task = _task_with_active_execution("task_acceptance")
    task.contract = task.contract.model_copy(
        update={"requires_human_acceptance": True}
    )
    task.executions[0].contract_snapshot = task.contract.model_copy(deep=True)
    runner = TaskGraphRunner(registry)

    state = runner._completion_judge(
        {
            "task": task,
            "round_plan": RoundPlan(
                should_continue=False,
                reason="Automatic work completed",
                final_output="Reviewable delivery",
            ),
            "round_outputs": [],
            "paused": False,
        }
    )

    assert task.task_status == TaskStatus.RUNNING
    assert task.current_node == CurrentNode.HUMAN_INTERVENTION
    assert task.completion_report is not None
    assert task.completion_report.terminal_status == TaskStatus.RUNNING
    assert task.completion_report.criterion_results[0].status.value == "passed"
    assert task.executions[0].status == TaskStatus.RUNNING
    assert task.executions[0].finished_at is None
    assert runner._route_after_judge(state) == "end"


def test_task_graph_does_not_use_round_plan_mock_when_system_fallback_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )
    task = _task_with_active_execution(new_id("task"))
    task.content = "Create quote for customer D"
    task.title = "Create quote for customer D"
    task.description = "Prepare quote for customer D"

    with pytest.raises(RuntimeError, match="System mock fallback is disabled"):
        TaskGraphRunner(registry).run(task)


def test_agent_model_execution_error_uses_mock_when_system_fallback_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, task, subtask, agent = _agent_execution_context(tmp_path)

    def _raise_execution_error(*args):
        raise AgentModelExecutionError(attempts=3, last_error="temporary model failure")

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _raise_execution_error,
    )

    outcome = runner._execute_subtask(task, subtask, agent)

    assert outcome.completed is True
    assert outcome.output == f"{agent.name} completed task {task.id}"


def test_agent_model_execution_error_returns_failed_outcome_when_fallback_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    runner, task, subtask, agent = _agent_execution_context(tmp_path)
    sensitive_value = "workflow-test-sensitive-token"

    def _raise_execution_error(*args):
        raise AgentModelExecutionError(
            attempts=3,
            last_error=f"Authorization: Bearer {sensitive_value}",
        )

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _raise_execution_error,
    )

    outcome = runner._execute_subtask(task, subtask, agent)

    assert outcome.completed is False
    message = outcome.error
    assert message.startswith("Agent model execution failed")
    assert "initial execution" in message
    assert "3 attempts" in message
    assert "[REDACTED]" in message
    assert sensitive_value not in message
    assert "System mock fallback is disabled" not in message


def test_agent_model_followup_error_uses_mock_when_system_fallback_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, task, subtask, agent = _agent_execution_context(
        tmp_path,
        tools=[AgentTool(name="lookup", type="mock", config={"response": "lookup result"})],
    )
    model_calls = 0

    def _execute(*args):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return [ToolCall(tool_name="lookup", arguments={"query": "customer"})], ""
        raise AgentModelExecutionError(attempts=2, last_error="followup model failure")

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _execute,
    )

    outcome = runner._execute_subtask(task, subtask, agent)

    assert outcome.completed is True
    assert outcome.output == f"{agent.name} completed task {task.id}"
    assert model_calls == 2
    assert len(subtask.tool_results) == 1


def test_agent_model_followup_error_does_not_override_failed_tool_with_mock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "true")
    runner, task, subtask, agent = _agent_execution_context(tmp_path)
    model_calls = 0

    def _execute(_task, _subtask, _agent, tool_results):
        nonlocal model_calls
        model_calls += 1
        if not tool_results:
            return [
                ToolCall(
                    tool_name="missing_tool",
                    arguments={"query": "customer"},
                )
            ], ""
        assert tool_results[0].success is False
        raise AgentModelExecutionError(attempts=2, last_error="followup model failure")

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _execute,
    )

    outcome = runner._execute_subtask(task, subtask, agent)

    expected_error = f"Tool missing_tool is not registered for agent {agent.id}"
    assert outcome.completed is False
    assert outcome.error == expected_error
    assert outcome.output == ""
    assert model_calls == 2
    assert len(subtask.tool_results) == 1
    assert subtask.tool_results[0].error == expected_error


def test_agent_model_followup_error_returns_failed_outcome_without_reexecuting_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    runner, task, subtask, agent = _agent_execution_context(
        tmp_path,
        tools=[AgentTool(name="lookup", type="mock", config={"response": "lookup result"})],
    )
    model_calls = 0
    tool_executions = 0
    original_execute = runner.tool_executor.execute

    def _execute_model(*args):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return [ToolCall(tool_name="lookup", arguments={"query": "customer"})], ""
        raise AgentModelExecutionError(attempts=2, last_error="followup model failure")

    def _execute_tool(agent_arg, tool_call):
        nonlocal tool_executions
        tool_executions += 1
        return original_execute(agent_arg, tool_call)

    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        _execute_model,
    )
    monkeypatch.setattr(runner.tool_executor, "execute", _execute_tool)

    outcome = runner._execute_subtask(task, subtask, agent)

    assert outcome.completed is False
    message = outcome.error
    assert message.startswith("Agent model execution failed")
    assert "followup execution" in message
    assert "2 attempts" in message
    assert "followup model failure" in message
    assert "System mock fallback is disabled" not in message
    assert model_calls == 2
    assert tool_executions == 1


def test_followup_error_preserves_successful_file_tool_result_and_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    output_path = tmp_path / "outputs" / "draft.md"
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Delivery Agent",
            description="Writes draft deliveries",
            capabilities=["delivery"],
            tools=[
                AgentTool(
                    name="write_delivery",
                    type="file_write",
                    config={"base_dir": str(output_path.parent)},
                )
            ],
        )
    )
    task = _task_with_active_execution("task_followup_file_failure")

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Write delivery draft",
                subtasks=[
                    SubTask(
                        id="write_draft",
                        title="Write delivery draft",
                        description="Write the delivery draft",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        raise AssertionError("planner must not run again after follow-up failure")

    def _execute(_task, _subtask, _agent, tool_results):
        if not tool_results:
            return [
                ToolCall(
                    tool_name="write_delivery",
                    arguments={"filename": "draft.md", "content": "draft body"},
                )
            ], ""
        raise AgentModelExecutionError(attempts=2, last_error="followup model failure")

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    result = TaskGraphRunner(registry).run(task)

    assert result.task_status == TaskStatus.FAILED
    assert len(result.context.rounds) == 1
    failed_subtask = result.context.rounds[0].subtasks[0]
    assert failed_subtask.status == TaskStatus.FAILED
    assert failed_subtask.output.startswith("Agent model execution failed during followup execution")
    assert len(failed_subtask.tool_results) == 1
    assert failed_subtask.tool_results[0].success is True
    assert failed_subtask.tool_results[0].result == str(output_path.resolve())
    assert output_path.read_text(encoding="utf-8") == "draft body"
    file_artifact = next(
        artifact
        for artifact in result.artifacts
        if artifact.kind == ArtifactKind.FILE
        and artifact.source_type == ArtifactSourceType.TOOL_RESULT
    )
    assert file_artifact.uri == output_path.resolve().as_uri()


@pytest.mark.parametrize(
    ("deliverable_kind", "deliverable_format", "expected_output"),
    [
        ("text", None, "short completion conclusion"),
        ("file", "markdown", "merged delivery body"),
    ],
)
def test_completion_judge_uses_delivery_content_for_criteria_and_finalize(
    tmp_path: Path,
    monkeypatch,
    deliverable_kind: str,
    deliverable_format: str | None,
    expected_output: str,
) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    task = _task_with_active_execution(f"task_completion_{deliverable_kind}")
    task.context.summary = "merged delivery body"
    task.context.rounds.append(
        TaskRound(
            round_index=1,
            execution_mode="sequential",
            reason="Delivery body completed",
            subtasks=[
                SubTask(
                    id=f"subtask_completion_{deliverable_kind}",
                    title="Complete delivery",
                    description="Complete the delivery body",
                    status=TaskStatus.SUCCEEDED,
                    output="merged delivery body",
                )
            ],
            context_after="merged delivery body",
        )
    )
    task.contract = task.contract.model_copy(
        update={
            "deliverable_kind": deliverable_kind,
            "deliverable_format": deliverable_format,
            "deliverable_filename": "delivery.md" if deliverable_kind == "file" else "",
        }
    )
    runner = TaskGraphRunner(registry)
    evaluated_outputs = []
    finalized_outputs = []

    def _evaluate(_task, output):
        evaluated_outputs.append(output)
        return []

    def _finalize(task_arg, *, output, criterion_results, **_kwargs):
        finalized_outputs.append((output, criterion_results))
        task_arg.task_status = TaskStatus.SUCCEEDED
        return SimpleNamespace(terminal_status=TaskStatus.SUCCEEDED)

    monkeypatch.setattr(runner.completion_service, "evaluate_criteria", _evaluate)
    monkeypatch.setattr(runner.completion_service, "finalize", _finalize)

    runner._completion_judge(
        {
            "task": task,
            "round_plan": RoundPlan(
                should_continue=False,
                reason="Work completed",
                final_output="short completion conclusion",
            ),
            "round_outputs": [],
            "paused": False,
        }
    )

    assert evaluated_outputs == [expected_output]
    assert finalized_outputs == [(expected_output, [])]


def test_task_graph_passes_only_processing_agents_to_planner(tmp_path: Path, monkeypatch) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    processing_agent = registry.create_agent(
        AgentCreate(name="Processing Agent", description="Handles work", capabilities=["work"])
    )
    registry.create_agent(
        AgentCreate(
            name="Canvas Human Node",
            description="Only used by workflow canvas",
            agent_type="human",
            capabilities=["approval"],
        )
    )
    task = _task_with_active_execution(new_id("task"))
    task.content = "Create quote for customer D"
    task.title = "Create quote for customer D"
    task.description = "Prepare quote for customer D"

    seen_agent_ids = []

    def _plan(task, agents):
        seen_agent_ids.extend(agent.id for agent in agents)
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                subtasks=[
                    SubTask(
                        id="subtask_processing",
                        title="Do work",
                        description="Do work",
                        assigned_agent_id=processing_agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: ([], "done"))

    TaskGraphRunner(registry).run(task)

    assert seen_agent_ids == [processing_agent.id, processing_agent.id]


def test_task_graph_executes_agent_tool_calls(tmp_path: Path, monkeypatch) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="CRM Agent",
            description="Handles CRM tasks",
            capabilities=["crm"],
            tools=[
                AgentTool(
                    name="crm_query",
                    type="mock",
                    config={"response": '{"customer_name": "Customer A", "level": "vip"}'},
                )
            ],
        )
    )
    task = _task_with_active_execution("task_tool")
    task.content = "Query CRM and prepare quote"
    task.title = "Query CRM"
    task.description = "Query customer_a from CRM"

    def _plan(task, agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need CRM data",
                subtasks=[
                    SubTask(
                        id="subtask_tool",
                        title="Query CRM",
                        description="Query customer_a from CRM",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        if not tool_results:
            return [ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"})], ""
        return [], f"Prepared quote for {tool_results[0].result}"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    result = TaskGraphRunner(registry).run(task)

    subtask = result.context.rounds[0].subtasks[0]
    assert subtask.tool_calls[0].tool_name == "crm_query"
    assert subtask.tool_results[0].success is True
    assert subtask.tool_results[0].result == '{"customer_name": "Customer A", "level": "vip"}'
    assert "Prepared quote" in subtask.output


def test_task_graph_routes_email_subtask_to_smtp_tool(tmp_path: Path, monkeypatch) -> None:
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert host == "smtp.example.com"
            assert port == 587
            assert timeout == 30

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            assert username == "sender@example.com"
            assert password == "secret"

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr("app.services.tool_executor.smtplib.SMTP", FakeSMTP)
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Email Agent",
            description="Handles sending emails to target recipients",
            capabilities=["email", "notification", "send_email"],
            tools=[
                AgentTool(
                    name="send_email",
                    type="smtp_email",
                    config={
                        "smtp_host": "smtp.example.com",
                        "smtp_port": "587",
                        "username": "sender@example.com",
                        "password": "secret",
                        "from": "sender@example.com",
                        "use_tls": "true",
                        "timeout_seconds": "30",
                    },
                )
            ],
        )
    )
    task = _task_with_active_execution("task_email")
    task.content = "请发送一封测试邮件给 minh@getui.com，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。"
    task.title = "发送测试邮件"
    task.description = "向 minh@getui.com 发送测试邮件"

    def _plan(task, agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need email notification",
                subtasks=[
                    SubTask(
                        id="subtask_email",
                        title="发送测试邮件",
                        description="向 minh@getui.com 发送测试邮件",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        if not tool_results:
            return [
                ToolCall(
                    tool_name="send_email",
                    arguments={
                        "to": "minh@getui.com",
                        "subject": "Agent 测试邮件",
                        "body": "这是任务协同中心发出的测试邮件。",
                    },
                )
            ], ""
        return [], f"邮件发送完成：{tool_results[0].result}"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    result = TaskGraphRunner(registry).run(task)

    subtask = result.context.rounds[0].subtasks[0]
    assert result.task_status == TaskStatus.SUCCEEDED
    assert subtask.assigned_agent_id == agent.id
    assert subtask.tool_calls[0].tool_name == "send_email"
    assert subtask.tool_results[0].success is True
    assert sent_messages[0]["To"] == "minh@getui.com"
    assert sent_messages[0]["Subject"] == "Agent 测试邮件"
    assert "邮件发送完成" in subtask.output


def test_failed_tool_call_marks_task_failed_and_records_failed_round(tmp_path: Path, monkeypatch) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="CRM Agent",
            description="Handles CRM tasks",
            capabilities=["crm"],
        )
    )
    task = Task(
        id="task_tool_failure",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Query CRM and prepare quote",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="Query CRM",
        description="Query customer_a from CRM",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    seen_contexts = []

    def _plan(task, agents):
        seen_contexts.append(task.context.summary)
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need CRM data",
                subtasks=[
                    SubTask(
                        id="subtask_tool_failure",
                        title="Query CRM",
                        description="Query customer_a from CRM",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        raise AssertionError("planner must not run again after a failed tool call")

    def _execute(task, subtask, agent, tool_results):
        if not tool_results:
            return [ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"})], ""
        return [], ""

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    result = TaskGraphRunner(registry).run(task)

    failed_subtask = result.context.rounds[0].subtasks[0]
    assert failed_subtask.status == TaskStatus.FAILED
    assert "Tool crm_query is not registered" in failed_subtask.output
    assert result.task_status == TaskStatus.FAILED
    assert result.loop_count == 1
    assert "Query CRM: Tool crm_query is not registered" in result.final_output
    assert seen_contexts == [""]


def test_empty_agent_output_marks_task_failed_and_records_failed_round(tmp_path: Path, monkeypatch) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )
    task = Task(
        id="task_empty_output",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create quote for customer D",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="Create quote for customer D",
        description="Prepare quote for customer D",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    def _plan(task, agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need quote",
                subtasks=[
                    SubTask(
                        id="subtask_empty_output",
                        title="Create quote",
                        description="Create quote for customer D",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        raise AssertionError("planner must not run again after an empty agent output")

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: ([], ""))

    result = TaskGraphRunner(registry).run(task)

    failed_subtask = result.context.rounds[0].subtasks[0]
    assert failed_subtask.status == TaskStatus.FAILED
    assert failed_subtask.output == "Agent returned no output"
    assert result.task_status == TaskStatus.FAILED
    assert "Create quote: Agent returned no output" in result.final_output


def test_failed_subtask_after_success_preserves_failure_output_for_file_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Report Agent",
            description="Prepares reports",
            capabilities=["report"],
        )
    )
    task = _task_with_active_execution("task_failed_after_success")
    task.contract = task.contract.model_copy(
        update={
            "deliverable_kind": "file",
            "deliverable_format": "markdown",
            "deliverable_filename": "delivery.md",
        }
    )
    task.executions[0].contract_snapshot = task.contract.model_copy(deep=True)
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="historical_success",
                    title="Draft report",
                    description="Draft report",
                    status=TaskStatus.SUCCEEDED,
                    output="Historical report body",
                )
            ],
            context_after="Historical report body",
        )
    ]
    task.context.summary = "Historical report body"
    task.loop_count = 2
    failing_subtask = SubTask(
        id="failing_subtask",
        title="Finalize report",
        description="Finalize report",
        assigned_agent_id=agent.id,
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda *_args: ([], ""),
    )

    state = TaskGraphRunner(registry)._subtask_execution(
        {
            "task": task,
            "round_plan": RoundPlan(
                should_continue=True,
                subtasks=[failing_subtask],
            ),
            "round_outputs": [],
            "paused": False,
        }
    )

    result = state["task"]
    failure_output = "Finalize report: Agent returned no output"
    assert result.task_status == TaskStatus.FAILED
    assert result.final_output == failure_output
    artifact = result.artifacts[-1]
    assert artifact.kind == ArtifactKind.TEXT
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.content == failure_output


def test_parallel_agent_subtasks_execute_concurrently_and_merge_context_in_plan_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )
    task = _task_with_active_execution("task_parallel_agents")
    task.content = "Run independent quote subtasks"
    task.title = "Run independent quote subtasks"
    task.description = "Run two independent agent subtasks"

    def _plan(task, agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                execution_mode="parallel",
                reason="Run independent subtasks",
                subtasks=[
                    SubTask(
                        id="subtask_slow",
                        title="Slow step",
                        description="Slow independent step",
                        assigned_agent_id=agent.id,
                    ),
                    SubTask(
                        id="subtask_fast",
                        title="Fast step",
                        description="Fast independent step",
                        assigned_agent_id=agent.id,
                    ),
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        if subtask.logical_key == "subtask_slow":
            time.sleep(0.2)
            return [], "slow output"
        time.sleep(0.2)
        return [], "fast output"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    started_at = time.monotonic()
    result = TaskGraphRunner(registry).run(task)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.35
    assert result.context.summary == "slow output\nfast output"
    assert [subtask.output for subtask in result.context.rounds[0].subtasks] == ["slow output", "fast output"]


def test_parallel_model_failure_preserves_plan_order_and_successful_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Parallel Agent",
            description="Runs parallel delivery steps",
            capabilities=["delivery"],
        )
    )
    task = _task_with_active_execution("task_parallel_model_failure")

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                execution_mode="parallel",
                reason="Run independent delivery steps",
                subtasks=[
                    SubTask(
                        id="successful_step",
                        title="Successful step",
                        description="Produce a successful output",
                        assigned_agent_id=agent.id,
                    ),
                    SubTask(
                        id="failed_step",
                        title="Failed step",
                        description="Fail during model execution",
                        assigned_agent_id=agent.id,
                    ),
                ],
            )
        raise AssertionError("planner must not run again after parallel model failure")

    def _execute(_task, subtask, _agent, _tool_results):
        if subtask.logical_key == "successful_step":
            return [], "successful output"
        raise AgentModelExecutionError(attempts=3, last_error="parallel model failure")

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    result = TaskGraphRunner(registry).run(task)

    assert result.task_status == TaskStatus.FAILED
    assert len(result.context.rounds) == 1
    subtasks = result.context.rounds[0].subtasks
    assert [subtask.logical_key for subtask in subtasks] == [
        "successful_step",
        "failed_step",
    ]
    assert [subtask.status for subtask in subtasks] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
    ]
    assert subtasks[0].output == "successful output"
    assert subtasks[1].output.startswith("Agent model execution failed during initial execution")
    successful_artifact = next(
        artifact
        for artifact in result.artifacts
        if artifact.source_type == ArtifactSourceType.SUBTASK_OUTPUT
        and artifact.source_id == subtasks[0].id
    )
    assert successful_artifact.content == "successful output"


def test_task_graph_registers_agent_output_file_and_tool_receipt_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Delivery Agent",
            description="Produces delivery artifacts",
            capabilities=["delivery"],
            tools=[
                AgentTool(
                    name="write_delivery",
                    type="file_write",
                    config={"base_dir": str(tmp_path / "outputs")},
                ),
                AgentTool(
                    name="crm_query",
                    type="mock",
                    config={"response": '{"customer": "A"}'},
                ),
            ],
        )
    )
    task = _task_with_active_execution("task_artifact_graph")

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                subtasks=[
                    SubTask(
                        id="subtask_artifact_graph",
                        title="Prepare delivery",
                        description="Prepare delivery artifacts",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    def _execute(_task, _subtask, _agent, tool_results):
        if not tool_results:
            return [
                ToolCall(
                    tool_name="write_delivery",
                    arguments={"filename": "delivery.md", "content": "file delivery"},
                ),
                ToolCall(tool_name="crm_query", arguments={"customer_id": "A"}),
            ], ""
        return [], "Agent delivery complete"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)
    artifact_service = ArtifactService()

    result = TaskGraphRunner(registry, artifact_service=artifact_service).run(task)

    assert result.task_status == TaskStatus.SUCCEEDED
    assert len(result.artifacts) == 4
    assert {artifact.source_type for artifact in result.artifacts} == {
        ArtifactSourceType.TASK_RESULT,
        ArtifactSourceType.SUBTASK_OUTPUT,
        ArtifactSourceType.TOOL_RESULT,
    }
    tool_artifacts = [
        artifact
        for artifact in result.artifacts
        if artifact.source_type == ArtifactSourceType.TOOL_RESULT
    ]
    assert {artifact.kind for artifact in tool_artifacts} == {
        ArtifactKind.FILE,
        ArtifactKind.TOOL_RESULT,
    }
    assert result.executions[0].artifacts == result.artifacts


def test_task_graph_registers_condition_subtask_output_artifact(tmp_path: Path, monkeypatch) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    task = _task_with_active_execution("task_condition_artifact")

    def _plan(task, _agents):
        if task.loop_count == 0:
            condition = SubTask(
                id="condition_artifact",
                title="Evaluate condition",
                description="Evaluate workflow condition",
                assignee_type="condition",
                result_metadata={"config": {"default_decision": "approved"}},
            )
            return RoundPlan(should_continue=True, subtasks=[condition])
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    artifact_service = ArtifactService()

    result = TaskGraphRunner(registry, artifact_service=artifact_service).run(task)

    condition_subtask = result.context.rounds[0].subtasks[0]
    condition_artifact = next(
        artifact
        for artifact in result.artifacts
        if artifact.source_type == ArtifactSourceType.SUBTASK_OUTPUT
        and artifact.source_id == condition_subtask.id
    )
    assert condition_artifact.source_type == ArtifactSourceType.SUBTASK_OUTPUT
    assert condition_artifact.content == "Condition decision: approved"
