from fastapi import APIRouter, HTTPException, Request

from app.api.auth import current_user
from app.core.models import ExecutionResultCreate, SubTask, Task
from app.services.task_service import PermissionDeniedError, SubTaskNotFoundError, WorkflowNotFoundError

router = APIRouter(prefix="/api/v1/subtasks", tags=["subtasks"])


@router.get("/human", response_model=list[SubTask])
def list_human_subtasks(request: Request, assignee_user_id: str | None = None) -> list[SubTask]:
    return request.app.state.task_service.list_human_subtasks(
        assignee_user_id=assignee_user_id,
        current_user=current_user(request),
    )


@router.post("/{subtask_id}/result", response_model=Task)
def submit_subtask_result(subtask_id: str, payload: ExecutionResultCreate, request: Request) -> Task:
    try:
        if payload.execution_mode == "async":
            task = request.app.state.task_service.submit_subtask_result(
                subtask_id,
                payload,
                resume_flow=False,
                current_user=current_user(request),
            )
            response_task = task.model_copy(deep=True)
            if task.current_node.value != "human_execution":
                request.app.state.task_service.start_background_task(task.id)
            return response_task
        return request.app.state.task_service.submit_subtask_result(
            subtask_id,
            payload,
            current_user=current_user(request),
        )
    except SubTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Subtask not found") from exc
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail="Subtask permission denied") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
