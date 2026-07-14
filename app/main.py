from pathlib import Path

from fastapi import FastAPI

from app.api.routes_agents import router as agents_router
from app.api.routes_tasks import router as tasks_router
from app.services.storage import AgentRegistry, InMemoryTaskStore
from app.services.task_service import TaskService


def create_app(agent_file: Path | None = None) -> FastAPI:
    app = FastAPI(title="TaskHub MVP", version="0.1.0")
    registry_file = agent_file or Path(__file__).resolve().parent / "data" / "agents.json"
    app.state.agent_registry = AgentRegistry(registry_file)
    app.state.task_store = InMemoryTaskStore()
    app.state.task_service = TaskService(app.state.task_store, app.state.agent_registry)
    app.include_router(agents_router)
    app.include_router(tasks_router)
    return app


app = create_app()
