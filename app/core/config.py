from __future__ import annotations

import os


DEFAULT_DATABASE_URL = ""


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
