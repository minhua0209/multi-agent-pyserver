from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DATABASE_URL: str | None = None
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "agent_outputs"


def get_agent_output_dir() -> Path:
    configured_path = os.getenv("AGENT_OUTPUT_DIR", "").strip()
    if not configured_path:
        return DEFAULT_AGENT_OUTPUT_DIR.resolve()

    output_dir = Path(configured_path).expanduser()
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    return output_dir.resolve()


def get_task_planner_type() -> str:
    planner_type = os.getenv("TASK_PLANNER_TYPE", "llm").lower()
    if planner_type not in {"llm", "crewai"}:
        return "llm"
    return planner_type


def is_default_database_enabled() -> bool:
    return os.getenv("DISABLE_DEFAULT_DATABASE_URL", "false").lower() not in {"1", "true", "yes", "on"}


def is_system_mock_fallback_enabled() -> bool:
    return os.getenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}


def require_system_mock_fallback_enabled(stage: str) -> None:
    if not is_system_mock_fallback_enabled():
        raise RuntimeError(f"System mock fallback is disabled at {stage}")
