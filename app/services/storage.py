from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete, select
from sqlalchemy.engine import Engine

from app.core.models import (
    Agent,
    AgentCreate,
    Event,
    SubTask,
    Task,
    TaskRound,
    WorkflowCreate,
    WorkflowTemplate,
    new_id,
    utc_now,
)


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


class WorkflowRegistry:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("[]")

    def list_workflows(self) -> list[WorkflowTemplate]:
        raw_workflows = json.loads(self.file_path.read_text())
        return [WorkflowTemplate.model_validate(raw_workflow) for raw_workflow in raw_workflows]

    def get_workflow(self, workflow_id: str) -> WorkflowTemplate | None:
        return next((workflow for workflow in self.list_workflows() if workflow.id == workflow_id), None)

    def create_workflow(self, payload: WorkflowCreate) -> WorkflowTemplate:
        workflows = self.list_workflows()
        now = utc_now()
        workflow = WorkflowTemplate(
            id=new_id("workflow"),
            status="active",
            created_at=now,
            updated_at=now,
            **payload.model_dump(by_alias=True),
        )
        workflows.append(workflow)
        self._write(workflows)
        return workflow

    def update_workflow(self, workflow_id: str, payload: WorkflowCreate) -> WorkflowTemplate | None:
        workflows = self.list_workflows()
        for index, workflow in enumerate(workflows):
            if workflow.id == workflow_id:
                updated = WorkflowTemplate(
                    id=workflow.id,
                    status=workflow.status,
                    created_at=workflow.created_at,
                    updated_at=utc_now(),
                    **payload.model_dump(by_alias=True),
                )
                workflows[index] = updated
                self._write(workflows)
                return updated
        return None

    def _write(self, workflows: list[WorkflowTemplate]) -> None:
        self.file_path.write_text(
            json.dumps(
                [item.model_dump(mode="json", by_alias=True) for item in workflows],
                indent=2,
                ensure_ascii=False,
            )
        )


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


metadata = MetaData()

agents_table = Table(
    "agents",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("payload", Text, nullable=True),
    Column("name", String(255), nullable=True),
    Column("description", Text, nullable=True),
    Column("capabilities_json", Text, nullable=True),
    Column("input_schema_json", Text, nullable=True),
    Column("output_schema_json", Text, nullable=True),
    Column("execution_config_json", Text, nullable=True),
    Column("tools_json", Text, nullable=True),
    Column("status", String(32), nullable=False, default="active"),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

task_requests_table = Table(
    "task_requests",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_type", String(32), nullable=True),
    Column("content", Text, nullable=True),
    Column("metadata_json", Text, nullable=True),
    Column("status", String(32), nullable=False, default="running"),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

tasks_table = Table(
    "tasks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("payload", Text, nullable=True),
    Column("request_id", String(64), nullable=True),
    Column("title", String(255), nullable=True),
    Column("description", Text, nullable=True),
    Column("status", String(32), nullable=False, default="running"),
    Column("current_node", String(64), nullable=True),
    Column("assigned_agent_id", String(64), nullable=True),
    Column("loop_count", Integer, nullable=False, default=0),
    Column("max_loop_count", Integer, nullable=False, default=10),
    Column("context_summary", Text, nullable=True),
    Column("final_output", Text, nullable=True),
    Column("draft_json", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

task_rounds_table = Table(
    "task_rounds",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=True),
    Column("round_index", Integer, nullable=True),
    Column("execution_mode", String(32), nullable=True),
    Column("reason", Text, nullable=True),
    Column("context_before", Text, nullable=True),
    Column("context_after", Text, nullable=True),
    Column("plan_json", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

subtasks_table = Table(
    "subtasks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=True),
    Column("round_id", String(64), nullable=True),
    Column("round_index", Integer, nullable=True),
    Column("title", String(255), nullable=True),
    Column("description", Text, nullable=True),
    Column("status", String(32), nullable=False, default="running"),
    Column("current_node", String(64), nullable=True),
    Column("assigned_agent_id", String(64), nullable=True),
    Column("assignee_type", String(32), nullable=False, default="agent"),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("max_retry_count", Integer, nullable=False, default=3),
    Column("output", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("tool_calls_json", Text, nullable=True),
    Column("tool_results_json", Text, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

task_events_table = Table(
    "task_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(64), nullable=True),
    Column("subtask_id", String(64), nullable=True),
    Column("event_type", String(64), nullable=True),
    Column("node_name", String(64), nullable=True),
    Column("message", Text, nullable=True),
    Column("payload_json", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
)

task_snapshots_table = Table(
    "task_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(64), nullable=True),
    Column("subtask_id", String(64), nullable=True),
    Column("round_id", String(64), nullable=True),
    Column("snapshot_type", String(64), nullable=True),
    Column("node_name", String(64), nullable=True),
    Column("snapshot_json", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
)

tool_executions_table = Table(
    "tool_executions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(64), nullable=True),
    Column("subtask_id", String(64), nullable=True),
    Column("agent_id", String(64), nullable=True),
    Column("tool_name", String(128), nullable=True),
    Column("tool_type", String(64), nullable=True),
    Column("arguments_json", Text, nullable=True),
    Column("success", Boolean, nullable=False, default=False),
    Column("result_text", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)

workflow_templates_table = Table(
    "workflow_templates",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("description", Text, nullable=True),
    Column("definition_json", Text, nullable=False),
    Column("status", String(32), nullable=False, default="active"),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)


def _create_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _round_id(task_id: str, round_index: int) -> str:
    return f"{task_id}_round_{round_index}"


class DatabaseAgentRegistry:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)

    def list_agents(self) -> list[Agent]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(agents_table.c.payload)).all()
        return [Agent.model_validate_json(row.payload) for row in rows]

    def create_agent(self, payload: AgentCreate) -> Agent:
        agent = Agent(id=new_id("agent"), created_at=utc_now(), **payload.model_dump())
        with self.engine.begin() as connection:
            connection.execute(
                agents_table.insert().values(
                    id=agent.id,
                    payload=agent.model_dump_json(),
                    name=agent.name,
                    description=agent.description,
                    capabilities_json=_json_dump(agent.capabilities),
                    input_schema_json=_json_dump(agent.input_schema),
                    output_schema_json=_json_dump(agent.output_schema),
                    execution_config_json=agent.execution_config.model_dump_json(),
                    tools_json=_json_dump([tool.model_dump(mode="json") for tool in agent.tools]),
                    status="active",
                    created_at=agent.created_at,
                    updated_at=agent.created_at,
                )
            )
        return agent


class DatabaseTaskStore:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)

    def save(self, task: Task) -> Task:
        task.updated_at = utc_now()
        payload = task.model_dump_json()
        with self.engine.begin() as connection:
            self._upsert_task_request(connection, task)
            existing = connection.execute(
                select(tasks_table.c.id).where(tasks_table.c.id == task.id)
            ).first()
            values = self._task_values(task, payload)
            if existing:
                connection.execute(
                    tasks_table.update().where(tasks_table.c.id == task.id).values(**values)
                )
            else:
                connection.execute(tasks_table.insert().values(**values))
            self._replace_task_children(connection, task)
        return task

    def get(self, task_id: str) -> Task | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(tasks_table.c.payload).where(tasks_table.c.id == task_id)
            ).first()
        if row is None:
            return None
        return Task.model_validate_json(row.payload)

    def list(self) -> list[Task]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(tasks_table.c.payload)).all()
        return [Task.model_validate_json(row.payload) for row in rows]

    @staticmethod
    def _task_values(task: Task, payload: str) -> dict:
        return {
            "id": task.id,
            "payload": payload,
            "request_id": task.request_id,
            "title": task.title,
            "description": task.description,
            "status": task.task_status.value,
            "current_node": task.current_node.value,
            "assigned_agent_id": task.assigned_agent_id,
            "loop_count": task.loop_count,
            "max_loop_count": task.max_loop_count,
            "context_summary": task.context.summary,
            "final_output": task.final_output,
            "draft_json": task.draft.model_dump_json() if task.draft else None,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    @staticmethod
    def _upsert_task_request(connection, task: Task) -> None:
        if not task.request_id:
            return
        existing = connection.execute(
            select(task_requests_table.c.id).where(task_requests_table.c.id == task.request_id)
        ).first()
        values = {
            "id": task.request_id,
            "source_type": task.source_type.value,
            "content": task.content,
            "metadata_json": _json_dump(task.request_metadata),
            "status": task.task_status.value,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }
        if existing:
            connection.execute(
                task_requests_table.update().where(task_requests_table.c.id == task.request_id).values(**values)
            )
        else:
            connection.execute(task_requests_table.insert().values(**values))

    def _replace_task_children(self, connection, task: Task) -> None:
        connection.execute(delete(task_rounds_table).where(task_rounds_table.c.task_id == task.id))
        connection.execute(delete(subtasks_table).where(subtasks_table.c.task_id == task.id))
        connection.execute(delete(task_events_table).where(task_events_table.c.task_id == task.id))
        connection.execute(delete(task_snapshots_table).where(task_snapshots_table.c.task_id == task.id))
        connection.execute(delete(tool_executions_table).where(tool_executions_table.c.task_id == task.id))

        for event in task.events:
            connection.execute(task_events_table.insert().values(**self._event_values(task, event)))

        for round_item in task.context.rounds:
            round_id = _round_id(task.id, round_item.round_index)
            connection.execute(task_rounds_table.insert().values(**self._round_values(task, round_item, round_id)))
            connection.execute(
                task_snapshots_table.insert().values(
                    task_id=task.id,
                    round_id=round_id,
                    snapshot_type="dispatch_output",
                    node_name="dispatch_decision",
                    snapshot_json=round_item.model_dump_json(),
                    created_at=task.updated_at,
                )
            )
            connection.execute(
                task_snapshots_table.insert().values(
                    task_id=task.id,
                    round_id=round_id,
                    snapshot_type="context_update",
                    node_name="context_update",
                    snapshot_json=_json_dump(
                        {
                            "context_before": round_item.context_before,
                            "context_after": round_item.context_after,
                        }
                    ),
                    created_at=task.updated_at,
                )
            )
            for subtask in round_item.subtasks:
                connection.execute(
                    subtasks_table.insert().values(**self._subtask_values(task, round_item, round_id, subtask))
                )
                connection.execute(
                    task_snapshots_table.insert().values(
                        task_id=task.id,
                        subtask_id=subtask.id,
                        round_id=round_id,
                        snapshot_type="subtask_execution_output",
                        node_name="subtask_execution",
                        snapshot_json=subtask.model_dump_json(),
                        created_at=task.updated_at,
                    )
                )
                for tool_result in subtask.tool_results:
                    connection.execute(
                        tool_executions_table.insert().values(
                            task_id=task.id,
                            subtask_id=subtask.id,
                            agent_id=subtask.assigned_agent_id,
                            tool_name=tool_result.tool_name,
                            tool_type=None,
                            arguments_json=_json_dump(tool_result.arguments),
                            success=tool_result.success,
                            result_text=tool_result.result,
                            error_message=tool_result.error,
                            started_at=task.updated_at,
                            finished_at=task.updated_at,
                        )
                    )

    @staticmethod
    def _event_values(task: Task, event: Event) -> dict:
        return {
            "task_id": task.id,
            "subtask_id": None,
            "event_type": event.type,
            "node_name": task.current_node.value,
            "message": event.message,
            "payload_json": event.model_dump_json(),
            "created_at": event.created_at,
        }

    @staticmethod
    def _round_values(task: Task, round_item: TaskRound, round_id: str) -> dict:
        return {
            "id": round_id,
            "task_id": task.id,
            "round_index": round_item.round_index,
            "execution_mode": round_item.execution_mode,
            "reason": round_item.reason,
            "context_before": round_item.context_before,
            "context_after": round_item.context_after,
            "plan_json": round_item.model_dump_json(),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    @staticmethod
    def _subtask_values(task: Task, round_item: TaskRound, round_id: str, subtask: SubTask) -> dict:
        return {
            "id": subtask.id,
            "task_id": task.id,
            "round_id": round_id,
            "round_index": round_item.round_index,
            "title": subtask.title,
            "description": subtask.description,
            "status": subtask.status.value,
            "current_node": subtask.current_node.value if subtask.current_node else "subtask_execution",
            "assigned_agent_id": subtask.assigned_agent_id,
            "assignee_type": subtask.assignee_type,
            "retry_count": 0,
            "max_retry_count": 3,
            "output": subtask.output,
            "error_message": subtask.output if subtask.status.value == "failed" else None,
            "tool_calls_json": _json_dump([tool_call.model_dump(mode="json") for tool_call in subtask.tool_calls]),
            "tool_results_json": _json_dump(
                [tool_result.model_dump(mode="json") for tool_result in subtask.tool_results]
            ),
            "started_at": task.updated_at,
            "finished_at": task.updated_at,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }


class DatabaseWorkflowRegistry:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)

    def list_workflows(self) -> list[WorkflowTemplate]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(workflow_templates_table)).mappings().all()
        return [self._row_to_workflow(row) for row in rows]

    def get_workflow(self, workflow_id: str) -> WorkflowTemplate | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(workflow_templates_table).where(workflow_templates_table.c.id == workflow_id)
            ).mappings().first()
        return self._row_to_workflow(row) if row else None

    def create_workflow(self, payload: WorkflowCreate) -> WorkflowTemplate:
        now = utc_now()
        workflow = WorkflowTemplate(
            id=new_id("workflow"),
            status="active",
            created_at=now,
            updated_at=now,
            **payload.model_dump(by_alias=True),
        )
        with self.engine.begin() as connection:
            connection.execute(workflow_templates_table.insert().values(**self._workflow_values(workflow)))
        return workflow

    def update_workflow(self, workflow_id: str, payload: WorkflowCreate) -> WorkflowTemplate | None:
        existing = self.get_workflow(workflow_id)
        if existing is None:
            return None
        workflow = WorkflowTemplate(
            id=workflow_id,
            status=existing.status,
            created_at=existing.created_at,
            updated_at=utc_now(),
            **payload.model_dump(by_alias=True),
        )
        with self.engine.begin() as connection:
            connection.execute(
                workflow_templates_table.update()
                .where(workflow_templates_table.c.id == workflow_id)
                .values(**self._workflow_values(workflow))
            )
        return workflow

    @staticmethod
    def _workflow_values(workflow: WorkflowTemplate) -> dict:
        return {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "definition_json": workflow.definition.model_dump_json(by_alias=True),
            "status": workflow.status,
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

    @staticmethod
    def _row_to_workflow(row) -> WorkflowTemplate:
        return WorkflowTemplate.model_validate(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"] or "",
                "definition": json.loads(row["definition_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
