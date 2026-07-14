import os
from pathlib import Path

from fastapi import FastAPI

from app.api.routes_agents import router as agents_router
from app.api.routes_subtasks import router as subtasks_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_workflows import router as workflows_router
from app.services.storage import (
    AgentRegistry,
    DatabaseAgentRegistry,
    DatabaseTaskStore,
    DatabaseWorkflowRegistry,
    InMemoryTaskStore,
    WorkflowRegistry,
)
from app.services.task_service import TaskService


def create_app(
    agent_file: Path | None = None,
    workflow_file: Path | None = None,
    database_url: str | None = None,
) -> FastAPI:
    app = FastAPI(title="TaskHub MVP", version="0.1.0")
    configured_database_url = database_url or os.getenv("DATABASE_URL")
    if configured_database_url:
        app.state.agent_registry = DatabaseAgentRegistry(configured_database_url)
        app.state.task_store = DatabaseTaskStore(configured_database_url)
        app.state.workflow_registry = DatabaseWorkflowRegistry(configured_database_url)
    else:
        registry_file = agent_file or Path(__file__).resolve().parent / "data" / "agents.json"
        app.state.agent_registry = AgentRegistry(registry_file)
        app.state.task_store = InMemoryTaskStore()
        workflow_registry_file = workflow_file or Path(__file__).resolve().parent / "data" / "workflows.json"
        app.state.workflow_registry = WorkflowRegistry(workflow_registry_file)
    app.state.task_service = TaskService(app.state.task_store, app.state.agent_registry, app.state.workflow_registry)
    app.include_router(agents_router)
    app.include_router(tasks_router)
    app.include_router(subtasks_router)
    app.include_router(workflows_router)
    return app


app = create_app()
