from pathlib import Path
import time

import pytest

from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    AgentCreate,
    AgentTool,
    RoundPlan,
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
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


def test_file_write_agent_uses_deterministic_tool_when_model_execution_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry = AgentRegistry(tmp_path / "agents.json")
    output_dir = tmp_path / "outputs"
    agent = registry.create_agent(
        AgentCreate(
            name="A2ADemo 文档写入助手",
            description="Writes reports to a configured local directory",
            capabilities=["write_report", "save_file"],
            tools=[
                AgentTool(
                    name="file_write",
                    type="file_write",
                    config={"base_dir": str(output_dir)},
                )
            ],
        )
    )
    task = _task_with_active_execution("task_weather_report")
    task.title = "天气报告"
    task.content = "查询最近一周天气情况，帮我写个简短的分析报告，写到A2ADemo的目录里面"
    task.context.summary = "最近一周天气：周一晴25°C，周二多云24°C，周三小雨22°C。"

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                execution_mode="sequential",
                subtasks=[
                    SubTask(
                        id="subtask_write_weather",
                        title="生成天气分析报告并写入A2ADemo目录",
                        description="基于查询到的天气数据，撰写简短分析报告，并保存到A2ADemo目录。",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: None)

    result = TaskGraphRunner(registry).run(task)

    written_file = output_dir / "reports" / "weather_report_7days.md"
    assert result.task_status == TaskStatus.SUCCEEDED
    assert written_file.exists()
    assert "周一晴25°C" in written_file.read_text(encoding="utf-8")
    subtask = result.context.rounds[0].subtasks[0]
    assert subtask.status == TaskStatus.SUCCEEDED
    assert subtask.tool_results[0].success is True
    assert str(written_file) in subtask.output


def test_task_graph_records_failed_agent_subtask_when_model_execution_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="General Agent",
            description="General processing without tools",
            capabilities=["general_processing"],
        )
    )
    task = _task_with_active_execution("task_model_unavailable")

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                execution_mode="sequential",
                subtasks=[
                    SubTask(
                        id="subtask_general",
                        title="生成分析报告",
                        description="生成分析报告",
                        assigned_agent_id=agent.id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: None)

    result = TaskGraphRunner(registry).run(task)

    assert result.task_status == TaskStatus.FAILED
    assert result.context.rounds[0].subtasks[0].status == TaskStatus.FAILED
    assert "模型执行失败" in result.context.rounds[0].subtasks[0].output


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

    condition_artifact = next(
        artifact
        for artifact in result.artifacts
        if artifact.source_id == result.context.rounds[0].subtasks[0].id
    )
    assert condition_artifact.source_type == ArtifactSourceType.SUBTASK_OUTPUT
    assert condition_artifact.content == "Condition decision: approved"
