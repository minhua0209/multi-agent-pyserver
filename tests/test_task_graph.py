from pathlib import Path

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
