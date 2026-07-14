from fastapi import APIRouter, HTTPException, Request

from app.core.models import ExecutionResultCreate, SubTask, Task
from app.services.task_service import SubTaskNotFoundError, WorkflowNotFoundError

router = APIRouter(prefix="/api/v1/subtasks", tags=["subtasks"])


@router.get("/human", response_model=list[SubTask])
def list_human_subtasks(request: Request) -> list[SubTask]:
    return request.app.state.task_service.list_human_subtasks()


@router.post("/{subtask_id}/result", response_model=Task)
def submit_subtask_result(subtask_id: str, payload: ExecutionResultCreate, request: Request) -> Task:
    try:
        return request.app.state.task_service.submit_subtask_result(subtask_id, payload)
    except SubTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Subtask not found") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
