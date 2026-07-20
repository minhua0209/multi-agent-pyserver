from copy import deepcopy
from typing import Any

from app.core.models import Artifact, Task, TaskContext, TaskExecution, TaskRerunResponse


REDACTED_ARGUMENT = "[REDACTED]"


def sanitize_execution(execution: TaskExecution) -> TaskExecution:
    sanitized = execution.model_copy(deep=True)
    _sanitize_context(sanitized.context_snapshot)
    _sanitize_artifacts(sanitized.artifacts)
    return sanitized


def sanitize_task(task: Task) -> Task:
    sanitized = task.model_copy(deep=True)
    _sanitize_context(sanitized.context)
    _sanitize_context(sanitized.initial_context)
    for execution in sanitized.executions:
        _sanitize_context(execution.context_snapshot)
        _sanitize_artifacts(execution.artifacts)
    _sanitize_artifacts(sanitized.artifacts)
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
        for subtask in round_item.subtasks:
            for tool_call in subtask.tool_calls:
                tool_call.arguments = _redact_arguments(tool_call.arguments)
            for tool_result in subtask.tool_results:
                tool_result.arguments = _redact_arguments(tool_result.arguments)


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
            if str(key).lower() == "arguments":
                sanitized[key] = (
                    _redact_arguments(item)
                    if isinstance(item, dict)
                    else REDACTED_ARGUMENT
                )
            else:
                sanitized[key] = _sanitize_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    return deepcopy(value)
