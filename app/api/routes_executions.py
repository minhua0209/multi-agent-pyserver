from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.api.auth import current_user, ensure_task_access
from app.api.serialization import (
    sanitize_execution,
    sanitize_rerun_response,
)
from app.core.enums import CurrentNode
from app.core.models import (
    TaskExecution,
    TaskRerunCreate,
    TaskRerunPreflightRequest,
    TaskRerunPreflightResponse,
    TaskRerunResponse,
)
from app.services.execution_service import (
    ExecutionNotFoundError,
    TaskRerunNotAllowedError,
    TaskRerunSideEffectConfirmationRequiredError,
)
from app.services.task_service import (
    TaskNotFoundError,
    TaskRerunIdempotencyConflictError,
    TaskRerunIdempotencyKeyRequiredError,
)


router = APIRouter(prefix="/api/v1/tasks/{task_id}/executions", tags=["executions"])


@router.get("", response_model=list[TaskExecution])
def list_task_executions(task_id: str, request: Request) -> list[TaskExecution]:
    try:
        task = request.app.state.task_service.get_task(task_id)
        ensure_task_access(current_user(request), task)
        return [
            sanitize_execution(execution)
            for execution in request.app.state.task_service.list_executions(task_id)
        ]
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/{execution_id}", response_model=TaskExecution)
def get_task_execution(
    task_id: str,
    execution_id: str,
    request: Request,
) -> TaskExecution:
    try:
        task = request.app.state.task_service.get_task(task_id)
        ensure_task_access(current_user(request), task)
        return sanitize_execution(
            request.app.state.task_service.get_execution(task_id, execution_id)
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except ExecutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Execution not found") from exc


@router.post("/preflight", response_model=TaskRerunPreflightResponse)
def preflight_task_rerun(
    task_id: str,
    payload: TaskRerunPreflightRequest,
    request: Request,
) -> TaskRerunPreflightResponse:
    try:
        task = request.app.state.task_service.get_task(task_id)
        ensure_task_access(current_user(request), task)
        preflight = request.app.state.task_service.preflight_rerun(task_id, payload)
        _raise_missing_source(preflight)
        return preflight
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.post("", response_model=TaskRerunResponse, status_code=status.HTTP_201_CREATED)
def create_task_rerun(
    task_id: str,
    payload: TaskRerunCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> TaskRerunResponse:
    try:
        actor = current_user(request)
        task = request.app.state.task_service.get_task(task_id)
        ensure_task_access(actor, task)
        rerun = request.app.state.task_service.create_rerun(
            task_id,
            payload,
            actor,
            idempotency_key,
        )
        if rerun.replayed:
            response.status_code = status.HTTP_200_OK
            return sanitize_rerun_response(rerun)

        execution_id = rerun.execution.id
        if rerun.task.current_node == CurrentNode.WAITING_DEPENDENCIES:
            return sanitize_rerun_response(rerun)
        if payload.execution_mode == "async":
            scheduled_task = request.app.state.task_service.schedule_confirmed_task(
                task_id,
                expected_execution_id=execution_id,
            )
            response_payload = TaskRerunResponse(
                task=scheduled_task.model_copy(deep=True),
                execution=request.app.state.task_service.get_execution(
                    task_id,
                    execution_id,
                ).model_copy(deep=True),
                replayed=False,
                scheduled=True,
                execution_is_active=scheduled_task.active_execution_id == execution_id,
            )
            request.app.state.task_service.start_background_task(
                task_id,
                expected_execution_id=execution_id,
            )
            return sanitize_rerun_response(response_payload)

        completed_task = request.app.state.task_service.run_confirmed_task(
            task_id,
            expected_execution_id=execution_id,
        )
        return sanitize_rerun_response(TaskRerunResponse(
            task=completed_task,
            execution=request.app.state.task_service.get_execution(
                task_id,
                execution_id,
            ),
            replayed=False,
            scheduled=False,
            execution_is_active=completed_task.active_execution_id == execution_id,
        ))
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskRerunIdempotencyKeyRequiredError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key must not be empty") from exc
    except TaskRerunIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency-Key payload conflict") from exc
    except TaskRerunSideEffectConfirmationRequiredError as exc:
        raise HTTPException(
            status_code=428,
            detail=exc.preflight.model_dump(mode="json"),
        ) from exc
    except TaskRerunNotAllowedError as exc:
        _raise_missing_source(exc.preflight)
        raise HTTPException(
            status_code=409,
            detail=exc.preflight.model_dump(mode="json"),
        ) from exc


def _raise_missing_source(preflight: TaskRerunPreflightResponse) -> None:
    if any(issue.code == "source_execution_not_found" for issue in preflight.issues):
        raise HTTPException(status_code=404, detail="Source execution not found")
