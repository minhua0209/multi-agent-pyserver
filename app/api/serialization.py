from copy import deepcopy
from typing import Any

from app.core.models import (
    Agent,
    Artifact,
    PublicAgent,
    PublicAgentTool,
    SubTask,
    Task,
    TaskContext,
    TaskExecution,
    TaskRequestResponse,
    TaskRerunResponse,
)


REDACTED_ARGUMENT = "[REDACTED]"


def public_agent(agent: Agent) -> PublicAgent:
    return PublicAgent(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        agent_type=agent.agent_type,
        capabilities=list(agent.capabilities),
        input_schema=deepcopy(agent.input_schema),
        output_schema=deepcopy(agent.output_schema),
        tools=[
            PublicAgentTool(
                name=tool.name,
                description=tool.description,
                type=tool.type,
                input_schema=deepcopy(tool.input_schema),
            )
            for tool in agent.tools
        ],
        created_at=agent.created_at,
    )


def sanitize_execution(execution: TaskExecution) -> TaskExecution:
    sanitized = execution.model_copy(deep=True)
    if sanitized.workflow_snapshot is not None:
        sanitized.workflow_snapshot = _sanitize_metadata(sanitized.workflow_snapshot)
    _sanitize_context(sanitized.context_snapshot)
    _sanitize_artifacts(sanitized.artifacts)
    return sanitized


def sanitize_task(task: Task) -> Task:
    sanitized = task.model_copy(deep=True)
    sanitized.request_metadata = _sanitize_metadata(sanitized.request_metadata)
    _sanitize_context(sanitized.context)
    _sanitize_context(sanitized.initial_context)
    for execution in sanitized.executions:
        if execution.workflow_snapshot is not None:
            execution.workflow_snapshot = _sanitize_metadata(execution.workflow_snapshot)
        _sanitize_context(execution.context_snapshot)
        _sanitize_artifacts(execution.artifacts)
    _sanitize_artifacts(sanitized.artifacts)
    return sanitized


def sanitize_task_request_response(response: TaskRequestResponse) -> TaskRequestResponse:
    return response.model_copy(
        update={"tasks": [sanitize_task(task) for task in response.tasks]},
        deep=True,
    )


def sanitize_subtask(subtask: SubTask) -> SubTask:
    sanitized = subtask.model_copy(deep=True)
    for tool_call in sanitized.tool_calls:
        tool_call.arguments = _redact_arguments(tool_call.arguments)
    for tool_result in sanitized.tool_results:
        tool_result.arguments = _redact_arguments(tool_result.arguments)
    sanitized.result_metadata = _sanitize_metadata(sanitized.result_metadata)
    return sanitized


def sanitize_rerun_response(response: TaskRerunResponse) -> TaskRerunResponse:
    return response.model_copy(
        update={
            "task": sanitize_task(response.task),
            "execution": sanitize_execution(response.execution),
        },
        deep=True,
    )


def _sanitize_context(context: TaskContext) -> None:
    for round_item in context.rounds:
        round_item.subtasks = [sanitize_subtask(subtask) for subtask in round_item.subtasks]


def _sanitize_artifacts(artifacts: list[Artifact]) -> None:
    for artifact in artifacts:
        artifact.metadata = _sanitize_metadata(artifact.metadata)


def _redact_arguments(arguments: dict) -> dict[str, str]:
    return {
        str(key): REDACTED_ARGUMENT
        for key in sorted(arguments, key=lambda item: str(item))
    }


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key == "private" or normalized_key.startswith("private_"):
                continue
            if normalized_key == "arguments":
                sanitized[key] = (
                    _redact_arguments(item)
                    if isinstance(item, dict)
                    else REDACTED_ARGUMENT
                )
            else:
                sanitized[key] = _sanitize_metadata(item)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_metadata(item) for item in value]
    return deepcopy(value)
