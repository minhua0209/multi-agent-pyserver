from pathlib import Path
import time

from app.core.enums import CurrentNode, SourceType, TaskStatus
from app.core.models import AgentCreate, AgentTool, RoundPlan, SubTask, Task, ToolCall, new_id, utc_now
from app.services.storage import AgentRegistry
from app.workflows.task_graph import TaskGraphRunner


def test_task_graph_runner_dispatches_executes_and_closes_task(tmp_path: Path) -> None:
    registry = AgentRegistry(tmp_path / "agents.json")
    agent = registry.create_agent(
        AgentCreate(
            name="Quote Agent",
            description="Handles quote tasks",
            capabilities=["quote"],
        )
    )
    task = Task(
        id=new_id("task"),
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Create quote for customer D",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="Create quote for customer D",
        description="Prepare quote for customer D",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

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
    task = Task(
        id="task_tool",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Query CRM and prepare quote",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="Query CRM",
        description="Query customer_a from CRM",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

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
    task = Task(
        id="task_email",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="请发送一封测试邮件给 minh@getui.com，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="发送测试邮件",
        description="向 minh@getui.com 发送测试邮件",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

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


def test_failed_tool_call_marks_subtask_failed_and_feeds_next_round(tmp_path: Path, monkeypatch) -> None:
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
        assert "FAILED: Query CRM" in task.context.summary
        assert "Tool crm_query is not registered" in task.context.summary
        return RoundPlan(should_continue=False, final_output=task.context.summary)

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
    assert result.task_status == TaskStatus.SUCCEEDED
    assert result.loop_count == 1
    assert seen_contexts == ["", result.context.summary]


def test_empty_agent_output_marks_subtask_failed_and_feeds_next_round(tmp_path: Path, monkeypatch) -> None:
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
        assert "FAILED: Create quote" in task.context.summary
        assert "Agent returned no output" in task.context.summary
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", lambda *args: ([], ""))

    result = TaskGraphRunner(registry).run(task)

    failed_subtask = result.context.rounds[0].subtasks[0]
    assert failed_subtask.status == TaskStatus.FAILED
    assert failed_subtask.output == "Agent returned no output"
    assert result.task_status == TaskStatus.SUCCEEDED


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
    task = Task(
        id="task_parallel_agents",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Run independent quote subtasks",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        title="Run independent quote subtasks",
        description="Run two independent agent subtasks",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

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
        if subtask.id == "subtask_slow":
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
