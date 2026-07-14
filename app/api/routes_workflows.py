from fastapi import APIRouter, HTTPException, Request, status

from app.core.models import WorkflowCreate, WorkflowTemplate

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowTemplate, status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreate, request: Request) -> WorkflowTemplate:
    return request.app.state.workflow_registry.create_workflow(payload)


@router.get("", response_model=list[WorkflowTemplate])
def list_workflows(request: Request) -> list[WorkflowTemplate]:
    return request.app.state.workflow_registry.list_workflows()


@router.get("/{workflow_id}", response_model=WorkflowTemplate)
def get_workflow(workflow_id: str, request: Request) -> WorkflowTemplate:
    workflow = request.app.state.workflow_registry.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.put("/{workflow_id}", response_model=WorkflowTemplate)
def update_workflow(workflow_id: str, payload: WorkflowCreate, request: Request) -> WorkflowTemplate:
    workflow = request.app.state.workflow_registry.update_workflow(workflow_id, payload)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow
