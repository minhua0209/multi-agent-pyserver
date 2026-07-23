from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import PurePath
from typing import Any, Literal
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CriterionResultStatus,
    CurrentNode,
    ExecutionTriggerType,
    ResultStatus,
    SourceType,
    TaskStatus,
    TaskType,
    UserRole,
)


MAX_AGENT_MODEL_RETRIES = 3


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def scoped_subtask_id(
    task_id: str,
    execution_id: str,
    logical_key: str,
    *,
    round_index: int = 0,
    ordinal: int = 0,
) -> str:
    identity = "\x00".join([task_id, execution_id, logical_key, str(round_index), str(ordinal)])
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"subtask_{digest}"


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
    metadata: dict[str, str] = Field(default_factory=dict)


class Agent(AgentCreate):
    id: str
    created_at: datetime


class PublicAgentTool(BaseModel):
    name: str
    description: str = ""
    type: str = "metadata"
    input_schema: dict = Field(default_factory=dict)


class PublicAgent(BaseModel):
    id: str
    name: str
    description: str = ""
    agent_type: str = "processing"
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    tools: list[PublicAgentTool] = Field(default_factory=list)
    created_at: datetime


class SimpleAgentCreate(BaseModel):
    ability: str = Field(min_length=1)
    name: str = ""


class HumanNodeCreate(BaseModel):
    name: str = Field(min_length=1)
    assignee_user_id: str = ""
    assignee_user_name: str = Field(min_length=1)
    assignee_role: str = "approver"


class MissingTool(BaseModel):
    type: str
    reason: str
    suggested_action: str = ""


class SimpleAgentCreateResponse(BaseModel):
    status: str
    message: str
    agent: PublicAgent | None = None
    matched_tools: list[str] = Field(default_factory=list)
    missing_tools: list[MissingTool] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)


class AgentExecutionConfig(BaseModel):
    system_prompt: str = ""
    model_name: str = ""
    temperature: float | None = None
    timeout_seconds: int = 60
    max_retries: int = Field(default=0, ge=0, le=MAX_AGENT_MODEL_RETRIES)
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
    task_type: TaskType | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskAttachment(BaseModel):
    id: str
    filename: str
    stored_filename: str = ""
    content_type: str = ""
    extension: str
    size_bytes: int
    text_preview: str = ""
    text_content: str = ""
    text_length: int = 0
    truncated: bool = False
    status: str = "parsed"
    error: str = ""
    created_by_user_id: str = ""
    created_by_user_name: str = ""
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    name: str = Field(min_length=1)
    phone: str = ""
    email: str = ""
    role: UserRole = UserRole.USER
    department: str = ""
    position: str = ""
    status: str = "active"
    remark: str = ""


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    role: UserRole | None = None
    department: str | None = None
    position: str | None = None
    status: str | None = None
    remark: str | None = None


class User(UserCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class UserOption(BaseModel):
    id: str
    name: str
    role: UserRole


class TaskContractItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    description: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def strip_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("description")
    @classmethod
    def require_non_empty_description(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned


class TaskContractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    deliverable_goal: str = Field(min_length=1)
    deliverable_kind: Literal["text", "file"] = "text"
    deliverable_format: Literal["markdown", "text"] | None = None
    deliverable_filename: str = ""
    deliverable_requirements: list[TaskContractItem] = Field(default_factory=list)
    success_criteria: list[TaskContractItem] = Field(min_length=1, max_length=10)
    requires_human_acceptance: bool = False

    @field_validator("goal", "deliverable_goal")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("deliverable_filename", mode="before")
    @classmethod
    def strip_deliverable_filename(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("deliverable_filename must be a plain filename")
        return value.strip()

    @model_validator(mode="after")
    def validate_deliverable_fields(self) -> "TaskContractInput":
        if self.deliverable_kind == "text":
            if self.deliverable_format is not None or self.deliverable_filename:
                raise ValueError("text deliverables cannot define file delivery fields")
            return self

        if self.deliverable_format is None:
            raise ValueError("deliverable_format is required for file deliverables")

        filename = self.deliverable_filename
        if filename.endswith(".") or any(character in filename for character in '<>:"/\\|?*'):
            raise ValueError("deliverable_filename must be a plain filename")

        device_basename = filename.partition(".")[0].upper()
        if device_basename in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(device_basename) == 4
            and device_basename[:3] in {"COM", "LPT"}
            and device_basename[3] in "123456789¹²³"
        ):
            raise ValueError("deliverable_filename must be a plain filename")

        expected_extension = ".md" if self.deliverable_format == "markdown" else ".txt"
        extension = PurePath(filename).suffix.lower()
        if extension and extension != expected_extension:
            raise ValueError(f"deliverable_filename extension must be {expected_extension}")
        if filename:
            resolved_filename = filename if extension else f"{filename}{expected_extension}"
            try:
                encoded_filename = resolved_filename.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("deliverable_filename must be valid UTF-8") from exc
            if len(encoded_filename) > 255:
                raise ValueError(
                    "deliverable_filename must be at most 255 UTF-8 bytes"
                )
        return self

    @model_validator(mode="after")
    def require_unique_item_ids(self) -> "TaskContractInput":
        for field_name in ("deliverable_requirements", "success_criteria"):
            item_ids = [item.id for item in getattr(self, field_name) if item.id]
            if len(item_ids) != len(set(item_ids)):
                raise ValueError(f"{field_name} IDs must be unique")
        return self


class TaskContract(TaskContractInput):
    version: int = 1
    confirmed_by_user_id: str = ""
    confirmed_by_user_name: str = ""
    confirmed_at: datetime
    legacy_inferred: bool = False


class TaskDraft(BaseModel):
    draft_key: str | None = None
    title: str
    description: str
    confidence: float
    suggested_assignee_type: str = "human"
    suggested_agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    goal: str = ""
    deliverable_goal: str = ""
    deliverable_kind: Literal["text", "file"] = "text"
    deliverable_format: Literal["markdown", "text"] | None = None
    deliverable_filename: str = ""
    deliverable_requirements: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    requires_human_acceptance: bool = False


class TaskRequestResponse(BaseModel):
    request_id: str
    tasks: list["Task"]


class TaskConfirm(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    contract: TaskContractInput | None = None
    execution_mode: str = "sync"
    default_assignee_user_id: str = ""
    default_assignee_user_name: str = ""
    default_assignee_role: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_contract_compatibility_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        contract = value.get("contract")
        if isinstance(contract, TaskContractInput):
            normalized_contract = contract.model_dump(
                include=set(TaskContractInput.model_fields)
            )
        elif isinstance(contract, dict):
            normalized_contract = dict(contract)
        else:
            return value
        normalized_contract.update(
            {
                "deliverable_kind": "text",
                "deliverable_format": None,
                "deliverable_filename": "",
                "requires_human_acceptance": False,
            }
        )
        normalized_value = dict(value)
        normalized_value["contract"] = normalized_contract
        return normalized_value


class ExecutionResultCreate(BaseModel):
    result_status: ResultStatus
    output: str = ""
    should_complete: bool = True
    metadata: dict = Field(default_factory=dict)
    execution_mode: str = "sync"
    completion_reason: str | None = None
    criterion_results: list["CriterionResult"] | None = None
    artifact_ids: list[str] | None = None

    @model_validator(mode="after")
    def require_unique_criterion_ids(self) -> "ExecutionResultCreate":
        criterion_ids = [result.criterion_id for result in self.criterion_results or []]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion result IDs must be unique")
        return self


class Event(BaseModel):
    type: str
    message: str
    created_at: datetime


class ToolCall(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool_execution_id: str = ""
    tool_name: str
    tool_type: str = ""
    side_effect: bool = False
    side_effect_known: bool = False
    arguments: dict = Field(default_factory=dict)
    success: bool
    result: str = ""
    error: str = ""


class SubTask(BaseModel):
    id: str
    execution_id: str = ""
    logical_key: str = ""
    title: str
    description: str
    task_id: str = ""
    task_title: str = ""
    task_description: str = ""
    task_content: str = ""
    task_context_summary: str = ""
    task_artifacts: list[str] = Field(default_factory=list)
    upstream_outputs: list[str] = Field(default_factory=list)
    assigned_agent_id: str | None = None
    assignee_type: str = "agent"
    assignee_user_id: str = ""
    assignee_user_name: str = ""
    assignee_role: str = ""
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


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_id: str = Field(min_length=1)
    status: CriterionResultStatus
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    evidence_text: str = ""
    reason: str = ""

    @field_validator("criterion_id", mode="before")
    @classmethod
    def strip_criterion_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class DeliverableResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    status: CriterionResultStatus
    artifact_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("requirement_id", mode="before")
    @classmethod
    def strip_requirement_id(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_unique_artifact_ids(self) -> "DeliverableResult":
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("deliverable result artifact IDs must be unique")
        return self


class CompletionReport(BaseModel):
    id: str
    execution_id: str = ""
    terminal_status: TaskStatus
    completion_reason: str
    criterion_results: list[CriterionResult] = Field(default_factory=list)
    deliverable_results: list[DeliverableResult] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    workflow_end_node_id: str | None = None
    human_accepted: bool = False
    awaiting_human_decision: bool = False
    automatic_gaps: list[str] = Field(default_factory=list)
    decided_by_type: str = "system"
    decided_by_id: str = ""
    decided_at: datetime
    evidence_summary: str = ""


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    kind: ArtifactKind
    source_type: ArtifactSourceType
    source_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    content: str = ""
    uri: str = ""
    media_type: str = ""
    checksum: str = ""
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PENDING
    validation_reason: str = ""
    deliverable_requirement_ids: list[str] = Field(default_factory=list)
    source_artifact_id: str | None = None
    reused_from_execution_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def validate_payload_and_requirements(self) -> "Artifact":
        if not self.content.strip() and not self.uri.strip():
            raise ValueError("artifact content or uri is required")
        if len(self.deliverable_requirement_ids) != len(set(self.deliverable_requirement_ids)):
            raise ValueError("deliverable requirement IDs must be unique")
        return self


class TaskExecution(BaseModel):
    id: str
    task_id: str
    attempt_no: int = Field(ge=1)
    trigger_type: ExecutionTriggerType
    trigger_reason: str = ""
    triggered_by_user_id: str = ""
    triggered_by_user_name: str = ""
    contract_snapshot: TaskContract | None = None
    workflow_snapshot: dict[str, Any] | None = None
    status: TaskStatus
    start_node: CurrentNode
    current_node: CurrentNode
    context_snapshot: TaskContext = Field(default_factory=TaskContext)
    artifacts: list[Artifact] = Field(default_factory=list)
    loop_count: int = 0
    final_output: str = ""
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_execution_id: str | None = None
    retry_of_execution_id: str | None = None
    idempotency_key: str = ""
    request_fingerprint: str = ""
    execution_mode: Literal["sync", "async"] = "sync"
    side_effects_confirmed_by_user_id: str = ""
    side_effects_confirmed_by_user_name: str = ""
    side_effects_confirmed_at: datetime | None = None
    completion_report: CompletionReport | None = None

    @field_validator("idempotency_key", "request_fingerprint", mode="before")
    @classmethod
    def strip_execution_identity(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("retry_of_execution_id", mode="before")
    @classmethod
    def strip_retry_execution_id(cls, value):
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        return cleaned or None


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
    task_type: TaskType = TaskType.AUTO_PLANNING
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str = ""
    created_by_user_name: str = ""
    task_status: TaskStatus
    current_node: CurrentNode
    draft: TaskDraft | None = None
    title: str | None = None
    description: str | None = None
    assigned_agent_id: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    contract: TaskContract | None = None
    context: TaskContext = Field(default_factory=TaskContext)
    initial_context: TaskContext = Field(default_factory=TaskContext)
    executions: list[TaskExecution] = Field(default_factory=list)
    active_execution_id: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    completion_report: CompletionReport | None = None
    final_output: str = ""
    loop_count: int = 0
    max_loop_count: int = 10
    events: list[Event] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def infer_task_type_from_legacy_metadata(self) -> "Task":
        if self.request_metadata.get("execution_mode") == "workflow_template":
            self.task_type = TaskType.MANUAL_ORCHESTRATION
        if "initial_context" not in self.model_fields_set:
            self.initial_context = self.context.model_copy(deep=True)

        execution_ids = [execution.id for execution in self.executions]
        if len(execution_ids) != len(set(execution_ids)):
            raise ValueError("execution IDs must be unique")

        attempt_numbers = [execution.attempt_no for execution in self.executions]
        if len(attempt_numbers) != len(set(attempt_numbers)):
            raise ValueError("execution attempt numbers must be unique")

        if any(execution.task_id != self.id for execution in self.executions):
            raise ValueError("execution task_id must match task id")

        idempotency_keys = [
            execution.idempotency_key
            for execution in self.executions
            if execution.idempotency_key
        ]
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise ValueError("execution idempotency keys must be unique")

        executions_by_id = {execution.id: execution for execution in self.executions}
        for execution in self.executions:
            if execution.retry_of_execution_id is None:
                continue
            source_execution = executions_by_id.get(execution.retry_of_execution_id)
            if (
                source_execution is None
                or source_execution.attempt_no >= execution.attempt_no
            ):
                raise ValueError(
                    "retry_of_execution_id must reference an earlier task execution"
                )

        for execution in self.executions:
            artifact_ids = [artifact.id for artifact in execution.artifacts]
            source_keys = [
                (artifact.source_type, artifact.source_id)
                for artifact in execution.artifacts
            ]
            if len(artifact_ids) != len(set(artifact_ids)):
                raise ValueError("execution artifact IDs must be unique")
            if len(source_keys) != len(set(source_keys)):
                raise ValueError("execution artifact source keys must be unique")
            if any(
                artifact.task_id != self.id or artifact.execution_id != execution.id
                for artifact in execution.artifacts
            ):
                raise ValueError("execution artifacts must belong to their task and execution")
            requirement_ids = {
                requirement.id
                for requirement in (
                    execution.contract_snapshot.deliverable_requirements
                    if execution.contract_snapshot is not None
                    else []
                )
            }
            if any(
                not set(artifact.deliverable_requirement_ids).issubset(requirement_ids)
                for artifact in execution.artifacts
            ):
                raise ValueError(
                    "execution artifact deliverable requirements must belong to its contract snapshot"
                )

        if self.executions:
            if self.active_execution_id not in execution_ids:
                raise ValueError("active_execution_id must reference an execution")
        elif self.active_execution_id is not None:
            raise ValueError("active_execution_id must reference an execution")

        current_artifact_ids = [artifact.id for artifact in self.artifacts]
        current_source_keys = [
            (artifact.source_type, artifact.source_id)
            for artifact in self.artifacts
        ]
        if len(current_artifact_ids) != len(set(current_artifact_ids)):
            raise ValueError("current artifact IDs must be unique")
        if len(current_source_keys) != len(set(current_source_keys)):
            raise ValueError("current artifact source keys must be unique")
        if self.artifacts and any(
            artifact.task_id != self.id or artifact.execution_id != self.active_execution_id
            for artifact in self.artifacts
        ):
            raise ValueError("current artifacts must belong to the active execution")
        active_execution = next(
            (
                execution
                for execution in self.executions
                if execution.id == self.active_execution_id
            ),
            None,
        )
        active_requirement_ids = {
            requirement.id
            for requirement in (
                active_execution.contract_snapshot.deliverable_requirements
                if active_execution is not None and active_execution.contract_snapshot is not None
                else []
            )
        }
        if any(
            not set(artifact.deliverable_requirement_ids).issubset(active_requirement_ids)
            for artifact in self.artifacts
        ):
            raise ValueError(
                "current artifact deliverable requirements must belong to the active contract snapshot"
            )

        all_artifacts = [
            artifact
            for execution in self.executions
            for artifact in execution.artifacts
        ] + self.artifacts
        for artifact in all_artifacts:
            has_source_artifact = bool(artifact.source_artifact_id)
            has_source_execution = bool(artifact.reused_from_execution_id)
            if has_source_artifact != has_source_execution:
                raise ValueError("artifact reuse references must be provided together")
            if not has_source_artifact:
                continue
            source_execution = executions_by_id.get(artifact.reused_from_execution_id or "")
            artifact_execution = executions_by_id.get(artifact.execution_id)
            if (
                source_execution is None
                or artifact_execution is None
                or source_execution.attempt_no >= artifact_execution.attempt_no
            ):
                raise ValueError("artifact reuse must reference an older task execution")
            if not any(
                source.id == artifact.source_artifact_id
                and source.task_id == self.id
                and source.execution_id == source_execution.id
                for source in source_execution.artifacts
            ):
                raise ValueError("artifact reuse source must exist in the referenced task execution")
        return self


class RerunIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class RerunSideEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtask_id: str = ""
    tool_execution_id: str = ""
    tool_name: str = Field(min_length=1)
    tool_type: str = Field(min_length=1)
    argument_keys: list[str] = Field(default_factory=list)
    success: bool

    @field_validator("argument_keys", mode="before")
    @classmethod
    def normalize_argument_keys(cls, value):
        if value is None:
            return []
        return sorted({str(item) for item in value})


class TaskRerunPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_execution_id: str = Field(min_length=1)

    @field_validator("source_execution_id", mode="before")
    @classmethod
    def strip_source_execution_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRerunPreflightResponse(BaseModel):
    task_id: str
    source_execution_id: str
    next_attempt_no: int = Field(ge=1)
    dependencies_satisfied: bool
    start_node: CurrentNode
    will_wait_for_dependencies: bool
    allowed: bool
    issues: list[RerunIssue] = Field(default_factory=list)
    side_effects: list[RerunSideEffect] = Field(default_factory=list)
    requires_side_effect_confirmation: bool = False


class TaskRerunCreate(TaskRerunPreflightRequest):
    reason: str = Field(min_length=1)
    execution_mode: Literal["sync", "async"] = "sync"
    confirm_side_effects: bool = False

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRerunResponse(BaseModel):
    task: Task
    execution: TaskExecution
    replayed: bool = False
    scheduled: bool = False
    execution_is_active: bool = False
