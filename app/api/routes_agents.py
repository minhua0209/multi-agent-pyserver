from fastapi import APIRouter, Request, status

from app.core.models import Agent, AgentCreate, Task

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("", response_model=Agent, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, request: Request) -> Agent:
    return request.app.state.agent_registry.create_agent(payload)


@router.get("", response_model=list[Agent])
def list_agents(request: Request) -> list[Agent]:
    return request.app.state.agent_registry.list_agents()


@router.post("/{agent_id}/poll", response_model=list[Task])
def poll_agent_tasks(agent_id: str, request: Request) -> list[Task]:
    tasks = request.app.state.task_service.list_tasks()
    return [task for task in tasks if task.assigned_agent_id == agent_id]
