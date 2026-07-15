from __future__ import annotations

from typing import Protocol

from app.core.models import Agent, RoundPlan, SubTask, Task, new_id


class TaskPlanner(Protocol):
    def plan_next_round(self, task: Task, agents: list[Agent]) -> RoundPlan | None:
        pass


def round_plan_from_dict(data: dict, agents: list[Agent]) -> RoundPlan:
    subtasks = []
    for item in data.get("subtasks", []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        if not title or not description:
            continue
        subtasks.append(
            SubTask(
                id=new_id("subtask"),
                title=title,
                description=description,
                assignee_type="human" if item.get("assignee_type") == "human" else "agent",
                assigned_agent_id=_valid_agent_id(item.get("assigned_agent_id"), agents),
            )
        )
    return RoundPlan(
        should_continue=bool(data.get("should_continue", bool(subtasks))),
        execution_mode="sequential" if data.get("execution_mode") == "sequential" else "parallel",
        reason=str(data.get("reason", "")),
        final_output=str(data.get("final_output", "")),
        subtasks=subtasks,
    )


def _valid_agent_id(value, agents: list[Agent]) -> str | None:
    if not isinstance(value, str):
        return None
    return value if any(agent.id == value for agent in agents) else None
