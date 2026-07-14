from __future__ import annotations

import json
from pathlib import Path

from app.core.models import Agent, AgentCreate, Task, new_id, utc_now


class AgentRegistry:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("[]")

    def list_agents(self) -> list[Agent]:
        raw_agents = json.loads(self.file_path.read_text())
        return [Agent.model_validate(raw_agent) for raw_agent in raw_agents]

    def create_agent(self, payload: AgentCreate) -> Agent:
        agents = self.list_agents()
        agent = Agent(id=new_id("agent"), created_at=utc_now(), **payload.model_dump())
        agents.append(agent)
        self.file_path.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in agents],
                indent=2,
                ensure_ascii=False,
            )
        )
        return agent


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def save(self, task: Task) -> Task:
        task.updated_at = utc_now()
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self._tasks.values())
