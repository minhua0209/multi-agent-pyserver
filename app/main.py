import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_attachments import router as attachments_router
from app.api.routes_agents import router as agents_router
from app.api.routes_subtasks import router as subtasks_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_users import router as users_router
from app.api.routes_workflows import router as workflows_router
from app.core.config import DEFAULT_DATABASE_URL, is_default_database_enabled
from app.services.storage import (
    AgentRegistry,
    DatabaseAgentRegistry,
    DatabaseTaskAttachmentStore,
    DatabaseTaskStore,
    DatabaseUserRegistry,
    DatabaseWorkflowRegistry,
    InMemoryTaskStore,
    TaskAttachmentStore,
    UserRegistry,
    WorkflowRegistry,
)
from app.services.task_service import TaskService


def create_app(
    agent_file: Path | None = None,
    workflow_file: Path | None = None,
    user_file: Path | None = None,
    attachment_file: Path | None = None,
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
        app.state.user_registry = DatabaseUserRegistry(configured_database_url)
        app.state.attachment_store = DatabaseTaskAttachmentStore(configured_database_url)
    else:
        registry_file = agent_file or Path(__file__).resolve().parent / "data" / "agents.json"
        app.state.agent_registry = AgentRegistry(registry_file)
        app.state.task_store = InMemoryTaskStore()
        workflow_registry_file = workflow_file or Path(__file__).resolve().parent / "data" / "workflows.json"
        app.state.workflow_registry = WorkflowRegistry(workflow_registry_file)
        user_registry_file = user_file or (registry_file.parent / "users.json")
        app.state.user_registry = UserRegistry(user_registry_file)
        attachment_registry_file = attachment_file or (registry_file.parent / "task_attachments.json")
        app.state.attachment_store = TaskAttachmentStore(attachment_registry_file)
    app.state.task_service = TaskService(
        app.state.task_store,
        app.state.agent_registry,
        app.state.workflow_registry,
        app.state.user_registry,
        app.state.attachment_store,
    )
    app.include_router(agents_router)
    app.include_router(attachments_router)
    app.include_router(tasks_router)
    app.include_router(subtasks_router)
    app.include_router(users_router)
    app.include_router(workflows_router)
    return app


app = create_app()


def configure_model_environment_from_args(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model-api-key")
    parser.add_argument("--model-responses-api-url")
    parser.add_argument("--model-name")
    args, _ = parser.parse_known_args(argv)

    if args.model_api_key is not None:
        os.environ["MODEL_API_KEY"] = args.model_api_key
    if args.model_responses_api_url is not None:
        os.environ["MODEL_RESPONSES_API_URL"] = args.model_responses_api_url
    if args.model_name is not None:
        os.environ["MODEL_NAME"] = args.model_name


def run_dev_server(argv: list[str] | None = None) -> None:
    configure_model_environment_from_args(argv)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run_dev_server()
