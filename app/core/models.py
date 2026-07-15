from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.enums import CurrentNode, ResultStatus, SourceType, TaskStatus


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    agent_type: str = "processing"
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    execution_config: "AgentExecutionConfig" = Field(default_factory=lambda: AgentExecutionConfig())
    tools: list["AgentTool"] = Field(default_factory=list)


class Agent(AgentCreate):
    id: str
    created_at: datetime


class SimpleAgentCreate(BaseModel):
    ability: str = Field(min_length=1)
    name: str = ""


class MissingTool(BaseModel):
    type: str
    reason: str
    suggested_action: str = ""


class SimpleAgentCreateResponse(BaseModel):
    status: str
    message: str
    agent: Agent | None = None
    matched_tools: list[str] = Field(default_factory=list)
    missing_tools: list[MissingTool] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)


class AgentExecutionConfig(BaseModel):
    system_prompt: str = ""
    model_name: str = ""
    temperature: float | None = None
    timeout_seconds: int = 60
    max_retries: int = 0
    max_tool_calls: int = 5


class AgentTool(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    type: str = "metadata"
    config: dict[str, str] = Field(default_factory=dict)
    input_schema: dict = Field(default_factory=dict)


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    agent_id: str | None = None
    config: dict = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    condition: dict = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    definition: WorkflowDefinition


class WorkflowTemplate(WorkflowCreate):
    id: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class TaskRequestCreate(BaseModel):
    source_type: SourceType
    title: str = Field(default="", max_length=50)
    content: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class TaskDraft(BaseModel):
    draft_key: str | None = None
    title: str
    description: str
    confidence: float
    suggested_assignee_type: str = "human"
    suggested_agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class TaskRequestResponse(BaseModel):
    request_id: str
    tasks: list["Task"]


class TaskConfirm(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    execution_mode: str = "sync"


class ExecutionResultCreate(BaseModel):
    result_status: ResultStatus
    output: str = ""
    should_complete: bool = True
    metadata: dict = Field(default_factory=dict)
    execution_mode: str = "sync"


class Event(BaseModel):
    type: str
    message: str
    created_at: datetime


class ToolCall(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    success: bool
    result: str = ""
    error: str = ""


class SubTask(BaseModel):
    id: str
    title: str
    description: str
    assigned_agent_id: str | None = None
    assignee_type: str = "agent"
    current_node: CurrentNode | None = None
    status: TaskStatus = TaskStatus.RUNNING
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolExecutionResult] = Field(default_factory=list)
    output: str = ""
    result_metadata: dict = Field(default_factory=dict)


class TaskRound(BaseModel):
    round_index: int
    execution_mode: str = "parallel"
    reason: str = ""
    context_before: str = ""
    subtasks: list[SubTask] = Field(default_factory=list)
    context_after: str = ""


class TaskContext(BaseModel):
    summary: str = ""
    rounds: list[TaskRound] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class RoundPlan(BaseModel):
    should_continue: bool = True
    execution_mode: str = "parallel"
    reason: str = ""
    subtasks: list[SubTask] = Field(default_factory=list)
    final_output: str = ""


class Task(BaseModel):
    id: str
    request_id: str | None = None
    source_type: SourceType
    content: str
    request_metadata: dict[str, str] = Field(default_factory=dict)
    task_status: TaskStatus
    current_node: CurrentNode
    draft: TaskDraft | None = None
    title: str | None = None
    description: str | None = None
    assigned_agent_id: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    context: TaskContext = Field(default_factory=TaskContext)
    final_output: str = ""
    loop_count: int = 0
    max_loop_count: int = 10
    events: list[Event] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
