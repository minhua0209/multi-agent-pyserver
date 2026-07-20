from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete, inspect, select, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import Engine

from app.core.models import (
    Agent,
    AgentCreate,
    Event,
    SubTask,
    Task,
    TaskAttachment,
    TaskRound,
    User,
    UserCreate,
    UserUpdate,
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

    def list_processing_agents(self) -> list[Agent]:
        return filter_processing_agents(self.list_agents())

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


def default_admin_user() -> User:
    now = utc_now()
    return User(
        id="root",
        name="管理员",
        phone="",
        email="",
        role="admin",
        department="平台",
        position="系统管理员",
        status="active",
        remark="默认管理员",
        created_at=now,
        updated_at=now,
    )


class UserRegistry:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._write([default_admin_user()])
        elif not self.list_users():
            self._write([default_admin_user()])
        elif self.get_user("root") is None:
            self._write([default_admin_user(), *self.list_users()])

    def list_users(self) -> list[User]:
        raw_users = json.loads(self.file_path.read_text())
        return [User.model_validate(raw_user) for raw_user in raw_users]

    def get_user(self, user_id: str) -> User | None:
        return next((user for user in self.list_users() if user.id == user_id), None)

    def create_user(self, payload: UserCreate) -> User:
        users = self.list_users()
        user = User(id=new_id("user"), created_at=utc_now(), updated_at=utc_now(), **payload.model_dump())
        users.append(user)
        self._write(users)
        return user

    def update_user(self, user_id: str, payload: UserUpdate) -> User | None:
        users = self.list_users()
        for index, user in enumerate(users):
            if user.id != user_id:
                continue
            changes = payload.model_dump(exclude_unset=True)
            updated = user.model_copy(update={**changes, "updated_at": utc_now()})
            users[index] = updated
            self._write(users)
            return updated
        return None

    def delete_user(self, user_id: str) -> bool:
        users = self.list_users()
        next_users = [user for user in users if user.id != user_id]
        if len(next_users) == len(users):
            return False
        self._write(next_users)
        return True

    def _write(self, users: list[User]) -> None:
        self.file_path.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in users],
                indent=2,
                ensure_ascii=False,
            )
        )


class TaskAttachmentStore:
    def __init__(self, file_path: Path, upload_dir: Path | None = None) -> None:
        self.file_path = file_path
        self.upload_dir = upload_dir or file_path.parent / "task_attachments"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("[]")

    def save_file(self, stored_filename: str, data: bytes) -> None:
        target_path = (self.upload_dir / stored_filename).resolve()
        if self.upload_dir.resolve() != target_path.parent:
            raise ValueError("Attachment file path must stay inside upload_dir")
        target_path.write_bytes(data)

    def save(self, attachment: TaskAttachment) -> TaskAttachment:
        attachments = self.list_attachments()
        attachments = [item for item in attachments if item.id != attachment.id]
        attachments.append(attachment)
        self.file_path.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in attachments],
                indent=2,
                ensure_ascii=False,
            )
        )
        return attachment

    def get(self, attachment_id: str) -> TaskAttachment | None:
        return next((attachment for attachment in self.list_attachments() if attachment.id == attachment_id), None)

    def list_attachments(self) -> list[TaskAttachment]:
        raw_attachments = json.loads(self.file_path.read_text())
        return [TaskAttachment.model_validate(raw_attachment) for raw_attachment in raw_attachments]


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

    def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None


metadata = MetaData()


def large_text():
    return Text().with_variant(LONGTEXT(), "mysql")


agents_table = Table(
    "agents",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("payload", Text, nullable=True),
    Column("name", String(255), nullable=True),
    Column("description", Text, nullable=True),
    Column("agent_type", String(64), nullable=False, default="processing"),
    Column("capabilities_json", Text, nullable=True),
    Column("input_schema_json", Text, nullable=True),
    Column("output_schema_json", Text, nullable=True),
    Column("execution_config_json", Text, nullable=True),
    Column("tools_json", Text, nullable=True),
    Column("metadata_json", Text, nullable=True),
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
    Column("created_by_user_id", String(64), nullable=True),
    Column("created_by_user_name", String(255), nullable=True),
    Column("status", String(32), nullable=False, default="running"),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

tasks_table = Table(
    "tasks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("payload", large_text(), nullable=True),
    Column("request_id", String(64), nullable=True),
    Column("title", String(255), nullable=True),
    Column("description", Text, nullable=True),
    Column("created_by_user_id", String(64), nullable=True),
    Column("created_by_user_name", String(255), nullable=True),
    Column("task_type", String(32), nullable=False, default="auto_planning"),
    Column("status", String(32), nullable=False, default="running"),
    Column("current_node", String(64), nullable=True),
    Column("assigned_agent_id", String(64), nullable=True),
    Column("loop_count", Integer, nullable=False, default=0),
    Column("max_loop_count", Integer, nullable=False, default=10),
    Column("context_summary", large_text(), nullable=True),
    Column("final_output", large_text(), nullable=True),
    Column("draft_json", large_text(), nullable=True),
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
    Column("context_before", large_text(), nullable=True),
    Column("context_after", large_text(), nullable=True),
    Column("plan_json", large_text(), nullable=True),
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
    Column("assignee_user_id", String(64), nullable=True),
    Column("assignee_user_name", String(255), nullable=True),
    Column("assignee_role", String(128), nullable=True),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("max_retry_count", Integer, nullable=False, default=3),
    Column("output", large_text(), nullable=True),
    Column("error_message", large_text(), nullable=True),
    Column("result_metadata_json", large_text(), nullable=True),
    Column("tool_calls_json", large_text(), nullable=True),
    Column("tool_results_json", large_text(), nullable=True),
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
    Column("payload_json", large_text(), nullable=True),
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
    Column("snapshot_json", large_text(), nullable=True),
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
    Column("result_text", large_text(), nullable=True),
    Column("error_message", large_text(), nullable=True),
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

users_table = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("phone", String(64), nullable=True),
    Column("email", String(255), nullable=True),
    Column("role", String(32), nullable=False, default="user"),
    Column("department", String(255), nullable=True),
    Column("position", String(255), nullable=True),
    Column("status", String(32), nullable=False, default="active"),
    Column("remark", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)

task_attachments_table = Table(
    "task_attachments",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("payload", large_text(), nullable=True),
    Column("filename", String(255), nullable=True),
    Column("stored_filename", String(255), nullable=True),
    Column("content_type", String(255), nullable=True),
    Column("extension", String(32), nullable=True),
    Column("size_bytes", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False, default="parsed"),
    Column("created_by_user_id", String(64), nullable=True),
    Column("created_by_user_name", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)


def _create_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _round_id(task_id: str, round_index: int) -> str:
    return f"{task_id}_round_{round_index}"


def filter_processing_agents(agents: list[Agent]) -> list[Agent]:
    return [agent for agent in agents if agent.agent_type == "processing"]


def _ensure_column(engine: Engine, table_name: str, column_name: str, definition: str) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def _ensure_mysql_longtext_columns(engine: Engine, table_name: str, column_names: list[str]) -> None:
    if engine.dialect.name != "mysql":
        return
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as connection:
        for column_name in column_names:
            if column_name in existing_columns:
                connection.execute(text(f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` LONGTEXT NULL"))


class DatabaseAgentRegistry:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)
        _ensure_column(self.engine, "agents", "agent_type", "VARCHAR(64) NOT NULL DEFAULT 'processing'")
        _ensure_column(self.engine, "agents", "metadata_json", "TEXT NULL")

    def list_agents(self) -> list[Agent]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(agents_table.c.payload)).all()
        return [Agent.model_validate_json(row.payload) for row in rows]

    def list_processing_agents(self) -> list[Agent]:
        return filter_processing_agents(self.list_agents())

    def create_agent(self, payload: AgentCreate) -> Agent:
        agent = Agent(id=new_id("agent"), created_at=utc_now(), **payload.model_dump())
        with self.engine.begin() as connection:
            connection.execute(
                agents_table.insert().values(
                    id=agent.id,
                    payload=agent.model_dump_json(),
                    name=agent.name,
                    description=agent.description,
                    agent_type=agent.agent_type,
                    capabilities_json=_json_dump(agent.capabilities),
                    input_schema_json=_json_dump(agent.input_schema),
                    output_schema_json=_json_dump(agent.output_schema),
                    execution_config_json=agent.execution_config.model_dump_json(),
                    tools_json=_json_dump([tool.model_dump(mode="json") for tool in agent.tools]),
                    metadata_json=_json_dump(agent.metadata),
                    status="active",
                    created_at=agent.created_at,
                    updated_at=agent.created_at,
                )
            )
        return agent


class DatabaseUserRegistry:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)
        self._ensure_default_admin()

    def list_users(self) -> list[User]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(users_table)).mappings().all()
        return [self._row_to_user(row) for row in rows]

    def get_user(self, user_id: str) -> User | None:
        with self.engine.begin() as connection:
            row = connection.execute(select(users_table).where(users_table.c.id == user_id)).mappings().first()
        return self._row_to_user(row) if row else None

    def create_user(self, payload: UserCreate) -> User:
        now = utc_now()
        user = User(id=new_id("user"), created_at=now, updated_at=now, **payload.model_dump())
        with self.engine.begin() as connection:
            connection.execute(users_table.insert().values(**self._user_values(user)))
        return user

    def update_user(self, user_id: str, payload: UserUpdate) -> User | None:
        existing = self.get_user(user_id)
        if existing is None:
            return None
        changes = payload.model_dump(exclude_unset=True)
        updated = existing.model_copy(update={**changes, "updated_at": utc_now()})
        with self.engine.begin() as connection:
            connection.execute(users_table.update().where(users_table.c.id == user_id).values(**self._user_values(updated)))
        return updated

    def delete_user(self, user_id: str) -> bool:
        if self.get_user(user_id) is None:
            return False
        with self.engine.begin() as connection:
            connection.execute(delete(users_table).where(users_table.c.id == user_id))
        return True

    def _ensure_default_admin(self) -> None:
        if self.get_user("root") is not None:
            return
        admin = default_admin_user()
        with self.engine.begin() as connection:
            connection.execute(users_table.insert().values(**self._user_values(admin)))

    @staticmethod
    def _user_values(user: User) -> dict:
        return {
            "id": user.id,
            "name": user.name,
            "phone": user.phone,
            "email": user.email,
            "role": user.role.value if hasattr(user.role, "value") else user.role,
            "department": user.department,
            "position": user.position,
            "status": user.status,
            "remark": user.remark,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }

    @staticmethod
    def _row_to_user(row) -> User:
        return User.model_validate(dict(row))


class DatabaseTaskAttachmentStore:
    def __init__(self, database_url: str, upload_dir: Path | None = None) -> None:
        self.engine = _create_engine(database_url)
        self.upload_dir = upload_dir or Path("./runtime/task_attachments")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        metadata.create_all(self.engine)
        _ensure_mysql_longtext_columns(self.engine, "task_attachments", ["payload"])

    def save_file(self, stored_filename: str, data: bytes) -> None:
        target_path = (self.upload_dir / stored_filename).resolve()
        if self.upload_dir.resolve() != target_path.parent:
            raise ValueError("Attachment file path must stay inside upload_dir")
        target_path.write_bytes(data)

    def save(self, attachment: TaskAttachment) -> TaskAttachment:
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(task_attachments_table.c.id).where(task_attachments_table.c.id == attachment.id)
            ).first()
            values = self._attachment_values(attachment)
            if existing:
                connection.execute(
                    task_attachments_table.update()
                    .where(task_attachments_table.c.id == attachment.id)
                    .values(**values)
                )
            else:
                connection.execute(task_attachments_table.insert().values(**values))
        return attachment

    def get(self, attachment_id: str) -> TaskAttachment | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(task_attachments_table.c.payload).where(task_attachments_table.c.id == attachment_id)
            ).first()
        if row is None:
            return None
        return TaskAttachment.model_validate_json(row.payload)

    def list_attachments(self) -> list[TaskAttachment]:
        with self.engine.begin() as connection:
            rows = connection.execute(select(task_attachments_table.c.payload)).all()
        return [TaskAttachment.model_validate_json(row.payload) for row in rows]

    @staticmethod
    def _attachment_values(attachment: TaskAttachment) -> dict:
        return {
            "id": attachment.id,
            "payload": attachment.model_dump_json(),
            "filename": attachment.filename,
            "stored_filename": attachment.stored_filename,
            "content_type": attachment.content_type,
            "extension": attachment.extension,
            "size_bytes": attachment.size_bytes,
            "status": attachment.status,
            "created_by_user_id": attachment.created_by_user_id,
            "created_by_user_name": attachment.created_by_user_name,
            "created_at": attachment.created_at,
            "updated_at": attachment.updated_at,
        }


class DatabaseTaskStore:
    def __init__(self, database_url: str) -> None:
        self.engine = _create_engine(database_url)
        metadata.create_all(self.engine)
        _ensure_column(self.engine, "subtasks", "result_metadata_json", "TEXT NULL")
        _ensure_column(self.engine, "tasks", "task_type", "VARCHAR(32) NOT NULL DEFAULT 'auto_planning'")
        _ensure_column(self.engine, "subtasks", "assignee_user_id", "VARCHAR(64) NULL")
        _ensure_column(self.engine, "subtasks", "assignee_user_name", "VARCHAR(255) NULL")
        _ensure_column(self.engine, "subtasks", "assignee_role", "VARCHAR(128) NULL")
        _ensure_column(self.engine, "tasks", "created_by_user_id", "VARCHAR(64) NULL")
        _ensure_column(self.engine, "tasks", "created_by_user_name", "VARCHAR(255) NULL")
        _ensure_column(self.engine, "task_requests", "created_by_user_id", "VARCHAR(64) NULL")
        _ensure_column(self.engine, "task_requests", "created_by_user_name", "VARCHAR(255) NULL")
        _ensure_mysql_longtext_columns(self.engine, "tasks", ["payload", "context_summary", "final_output", "draft_json"])
        _ensure_mysql_longtext_columns(self.engine, "task_rounds", ["context_before", "context_after", "plan_json"])
        _ensure_mysql_longtext_columns(
            self.engine,
            "subtasks",
            ["output", "error_message", "result_metadata_json", "tool_calls_json", "tool_results_json"],
        )
        _ensure_mysql_longtext_columns(self.engine, "task_events", ["payload_json"])
        _ensure_mysql_longtext_columns(self.engine, "task_snapshots", ["snapshot_json"])
        _ensure_mysql_longtext_columns(self.engine, "tool_executions", ["result_text", "error_message"])

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

    def delete(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        with self.engine.begin() as connection:
            connection.execute(delete(task_rounds_table).where(task_rounds_table.c.task_id == task_id))
            connection.execute(delete(subtasks_table).where(subtasks_table.c.task_id == task_id))
            connection.execute(delete(task_events_table).where(task_events_table.c.task_id == task_id))
            connection.execute(delete(task_snapshots_table).where(task_snapshots_table.c.task_id == task_id))
            connection.execute(delete(tool_executions_table).where(tool_executions_table.c.task_id == task_id))
            connection.execute(delete(tasks_table).where(tasks_table.c.id == task_id))
            if task.request_id:
                connection.execute(delete(task_requests_table).where(task_requests_table.c.id == task.request_id))
        return True

    @staticmethod
    def _task_values(task: Task, payload: str) -> dict:
        return {
            "id": task.id,
            "payload": payload,
            "request_id": task.request_id,
            "title": task.title,
            "description": task.description,
            "created_by_user_id": task.created_by_user_id,
            "created_by_user_name": task.created_by_user_name,
            "task_type": task.task_type.value,
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
            "created_by_user_id": task.created_by_user_id,
            "created_by_user_name": task.created_by_user_name,
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
                            tool_type=tool_result.tool_type or None,
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
            "assignee_user_id": subtask.assignee_user_id,
            "assignee_user_name": subtask.assignee_user_name,
            "assignee_role": subtask.assignee_role,
            "retry_count": 0,
            "max_retry_count": 3,
            "output": subtask.output,
            "error_message": subtask.output if subtask.status.value == "failed" else None,
            "result_metadata_json": _json_dump(subtask.result_metadata),
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
