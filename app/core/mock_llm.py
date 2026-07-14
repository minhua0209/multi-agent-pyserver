from app.core.models import Agent, RoundPlan, SubTask, Task, TaskDraft, new_id


def mock_intent_recognition(content: str) -> TaskDraft:
    return TaskDraft(
        title=content.strip(),
        description=f"Mock extracted task from request: {content.strip()}",
        confidence=0.9,
    )


def mock_intent_recognitions(content: str, agents: list[Agent] | None = None) -> list[TaskDraft]:
    return [mock_intent_recognition(content)]


def mock_dispatch(task: Task, agents: list[Agent]) -> Agent | None:
    text = f"{task.title or ''} {task.description or ''} {task.content}".lower()
    for agent in agents:
        if any(capability.lower() in text for capability in agent.capabilities):
            return agent
    return None


def mock_agent_execution(task: Task, agent: Agent) -> str:
    return f"{agent.name} completed task {task.id}"


def mock_human_node_processing(task: Task) -> str:
    return f"Human node processed task {task.id}"


def mock_completion_judge(task: Task, execution_output: str) -> bool:
    if task.loop_count > task.max_loop_count:
        return False
    return bool(execution_output.strip())


def mock_round_plan(task: Task, agents: list[Agent]) -> RoundPlan:
    if task.context.rounds:
        return RoundPlan(should_continue=False, reason="Mock dispatcher found no remaining subtasks")
    agent = mock_dispatch(task, agents)
    return RoundPlan(
        should_continue=True,
        execution_mode="parallel",
        reason="Mock dispatcher created one subtask",
        subtasks=[
            SubTask(
                id=new_id("subtask"),
                title=task.title or task.content,
                description=task.description or task.content,
                assigned_agent_id=agent.id if agent else None,
            )
        ],
    )
