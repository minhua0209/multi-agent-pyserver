from fastapi import APIRouter, Request, Response, status

from app.api.serialization import public_agent, sanitize_task
from app.core.models import (
    AgentCreate,
    HumanNodeCreate,
    PublicAgent,
    SimpleAgentCreate,
    SimpleAgentCreateResponse,
    Task,
)
from app.services.agent_profile_builder import AgentProfileBuilder

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("", response_model=PublicAgent, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, request: Request) -> PublicAgent:
    return public_agent(request.app.state.agent_registry.create_agent(payload))


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
        agent=public_agent(agent),
        matched_tools=result.matched_tools,
        missing_tools=result.missing_tools,
        guidance=result.guidance,
    )


@router.post("/human-node", response_model=SimpleAgentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_human_node(payload: HumanNodeCreate, request: Request) -> SimpleAgentCreateResponse:
    assignee_name = payload.assignee_user_name.strip()
    assignee_user_id = payload.assignee_user_id.strip() or assignee_name
    agent_create = AgentCreate(
        name=payload.name.strip(),
        description=f"人工审批节点，审批人：{assignee_name}",
        agent_type="human",
        capabilities=["human_approval"],
        metadata={
            "assignee_user_id": assignee_user_id,
            "assignee_user_name": assignee_name,
            "assignee_role": payload.assignee_role.strip() or "approver",
        },
    )
    agent = request.app.state.agent_registry.create_agent(agent_create)
    return SimpleAgentCreateResponse(
        status="created",
        message="人工节点已创建。",
        agent=public_agent(agent),
    )


@router.get("", response_model=list[PublicAgent])
def list_agents(request: Request) -> list[PublicAgent]:
    return [public_agent(agent) for agent in request.app.state.agent_registry.list_agents()]


@router.post("/{agent_id}/poll", response_model=list[Task])
def poll_agent_tasks(agent_id: str, request: Request) -> list[Task]:
    tasks = request.app.state.task_service.list_tasks()
    return [sanitize_task(task) for task in tasks if task.assigned_agent_id == agent_id]
