from fastapi import APIRouter, HTTPException, Request, status

from app.api.auth import current_user, ensure_task_access, filter_tasks_for_user
from app.api.serialization import sanitize_task, sanitize_task_request_response
from app.core.enums import CurrentNode
from app.core.models import ExecutionResultCreate, Task, TaskConfirm, TaskRequestCreate, TaskRequestResponse
from app.services.task_service import (
    AttachmentNotFoundError,
    HumanAcceptanceNotPendingError,
    TaskAlreadyConfirmedError,
    TaskCannotBeCancelledError,
    TaskNotFoundError,
    TaskNotConfirmedError,
    TaskNotRunningError,
    TaskResultNotAllowedError,
    WorkflowNotFoundError,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/requests", response_model=TaskRequestResponse, status_code=status.HTTP_201_CREATED)
def create_task_request(payload: TaskRequestCreate, request: Request) -> TaskRequestResponse:
    try:
        return sanitize_task_request_response(
            request.app.state.task_service.create_request(payload, current_user(request))
        )
    except AttachmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Attachment not found: {exc}") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.post("/{task_id}/confirm", response_model=Task)
def confirm_task(task_id: str, payload: TaskConfirm, request: Request) -> Task:
    try:
        user = current_user(request)
        ensure_task_access(user, request.app.state.task_service.get_task(task_id))
        if payload.execution_mode == "async":
            task = request.app.state.task_service.confirm_task_details(task_id, payload, confirmed_by=user)
            if task.current_node != CurrentNode.WAITING_DEPENDENCIES:
                execution_id = task.active_execution_id
                task = request.app.state.task_service.schedule_confirmed_task(
                    task.id,
                    expected_execution_id=execution_id,
                )
                response_task = task.model_copy(deep=True)
                request.app.state.task_service.start_background_task(
                    task.id,
                    expected_execution_id=execution_id,
                )
                return sanitize_task(response_task)
            return sanitize_task(task)
        return sanitize_task(
            request.app.state.task_service.confirm_task(task_id, payload, confirmed_by=user)
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskAlreadyConfirmedError as exc:
        raise HTTPException(status_code=409, detail="Task contract is already confirmed") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.post("/{task_id}/result", response_model=Task)
def submit_task_result(task_id: str, payload: ExecutionResultCreate, request: Request) -> Task:
    try:
        user = current_user(request)
        ensure_task_access(user, request.app.state.task_service.get_task(task_id))
        return sanitize_task(
            request.app.state.task_service.submit_result(task_id, payload, current_user=user)
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskNotRunningError as exc:
        raise HTTPException(status_code=409, detail="Task is not running") from exc
    except TaskNotConfirmedError as exc:
        raise HTTPException(status_code=409, detail="Task is not confirmed") from exc
    except TaskResultNotAllowedError as exc:
        raise HTTPException(
            status_code=409,
            detail="Task result is only accepted during human intervention",
        ) from exc
    except HumanAcceptanceNotPendingError as exc:
        raise HTTPException(
            status_code=409,
            detail="Human acceptance requires the active execution pending report",
        ) from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_task(task_id: str, request: Request) -> None:
    try:
        user = current_user(request)
        ensure_task_access(user, request.app.state.task_service.get_task(task_id))
        request.app.state.task_service.cancel_unconfirmed_task(task_id, cancelled_by=user)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except TaskCannotBeCancelledError as exc:
        raise HTTPException(status_code=409, detail="Only unconfirmed tasks can be cancelled") from exc


@router.get("/{task_id}", response_model=Task)
def get_task(task_id: str, request: Request) -> Task:
    try:
        task = request.app.state.task_service.get_task(task_id)
        ensure_task_access(current_user(request), task)
        return sanitize_task(task)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("", response_model=list[Task])
def list_tasks(request: Request) -> list[Task]:
    return [
        sanitize_task(task)
        for task in filter_tasks_for_user(
            current_user(request),
            request.app.state.task_service.list_tasks(),
        )
    ]
