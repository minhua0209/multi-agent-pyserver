from fastapi import APIRouter, Request, Response, status

from app.core.models import Agent, AgentCreate, SimpleAgentCreate, SimpleAgentCreateResponse, Task
from app.services.agent_profile_builder import AgentProfileBuilder

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("", response_model=Agent, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, request: Request) -> Agent:
    return request.app.state.agent_registry.create_agent(payload)


@router.post("/simple", response_model=SimpleAgentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_simple_agent(payload: SimpleAgentCreate, request: Request, response: Response) -> SimpleAgentCreateResponse:
    result = AgentProfileBuilder().build(payload)
    if result.agent_create is None:
        response.status_code = status.HTTP_200_OK
        return SimpleAgentCreateResponse(
            status=result.status,
            message=result.message,
            matched_tools=result.matched_tools,
            missing_tools=result.missing_tools,
            guidance=result.guidance,
        )

    agent = request.app.state.agent_registry.create_agent(result.agent_create)
    return SimpleAgentCreateResponse(
        status="created",
        message=result.message,
        agent=agent,
        matched_tools=result.matched_tools,
        missing_tools=result.missing_tools,
        guidance=result.guidance,
    )


@router.get("", response_model=list[Agent])
def list_agents(request: Request) -> list[Agent]:
    return request.app.state.agent_registry.list_agents()


@router.post("/{agent_id}/poll", response_model=list[Task])
def poll_agent_tasks(agent_id: str, request: Request) -> list[Task]:
    tasks = request.app.state.task_service.list_tasks()
    return [task for task in tasks if task.assigned_agent_id == agent_id]
