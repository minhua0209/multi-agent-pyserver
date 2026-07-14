import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_agents import router as agents_router
from app.api.routes_subtasks import router as subtasks_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_workflows import router as workflows_router
from app.core.config import DEFAULT_DATABASE_URL, is_default_database_enabled
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    default_database_url = None
    if not agent_file and not workflow_file and is_default_database_enabled():
        default_database_url = DEFAULT_DATABASE_URL
    configured_database_url = database_url or os.getenv("DATABASE_URL") or default_database_url
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


def run_dev_server() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run_dev_server()
