from pathlib import Path
from urllib.parse import urlsplit

from app.core.enums import CurrentNode, SourceType, TaskStatus
from app.core.model_client import DEFAULT_RESPONSES_API_URL
from app.core.models import Task, utc_now
from app.main import create_app


def test_default_model_url_is_credential_free_loopback_placeholder() -> None:
    parsed = urlsplit(DEFAULT_RESPONSES_API_URL)

    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/v1/responses"
    ):
        raise AssertionError("default model URL must be a credential-free loopback placeholder")


def test_create_app_without_database_url_keeps_task_lifecycle_isolated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app_files = {
        "agent_file": tmp_path / "agents.json",
        "workflow_file": tmp_path / "workflows.json",
        "user_file": tmp_path / "users.json",
        "attachment_file": tmp_path / "attachments.json",
    }
    first_app = create_app(**app_files)
    second_app = create_app(**app_files)
    now = utc_now()
    first_app.state.task_store.save(
        Task(
            id="task_first_app_only",
            source_type=SourceType.BUSINESS_SYSTEM,
            content="isolated task",
            task_status=TaskStatus.RUNNING,
            current_node=CurrentNode.HUMAN_CONFIRMATION,
            created_at=now,
            updated_at=now,
        )
    )

    assert [task.id for task in first_app.state.task_store.list()] == [
        "task_first_app_only"
    ]
    assert second_app.state.task_store.list() == []
