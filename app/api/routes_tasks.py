from fastapi import APIRouter, HTTPException, Request, status

from app.core.enums import CurrentNode
from app.core.models import ExecutionResultCreate, Task, TaskConfirm, TaskRequestCreate, TaskRequestResponse
from app.services.task_service import TaskNotFoundError, WorkflowNotFoundError

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("/requests", response_model=TaskRequestResponse, status_code=status.HTTP_201_CREATED)
def create_task_request(payload: TaskRequestCreate, request: Request) -> TaskRequestResponse:
    return request.app.state.task_service.create_request(payload)


@router.post("/{task_id}/confirm", response_model=Task)
def confirm_task(task_id: str, payload: TaskConfirm, request: Request) -> Task:
    try:
        if payload.execution_mode == "async":
            task = request.app.state.task_service.confirm_task_details(task_id, payload)
            if task.current_node != CurrentNode.WAITING_DEPENDENCIES:
                task = request.app.state.task_service.schedule_confirmed_task(task.id)
                response_task = task.model_copy(deep=True)
                request.app.state.task_service.start_background_task(task.id)
                return response_task
            return task
        return request.app.state.task_service.confirm_task(task_id, payload)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.post("/{task_id}/result", response_model=Task)
def submit_task_result(task_id: str, payload: ExecutionResultCreate, request: Request) -> Task:
    try:
        return request.app.state.task_service.submit_result(task_id, payload)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.get("/{task_id}", response_model=Task)
def get_task(task_id: str, request: Request) -> Task:
    try:
        return request.app.state.task_service.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("", response_model=list[Task])
def list_tasks(request: Request) -> list[Task]:
    return request.app.state.task_service.list_tasks()
