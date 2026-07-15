from __future__ import annotations

from app.core.config import get_task_planner_type
from app.planners.crewai_planner import CrewAITaskPlanner
from app.planners.llm_planner import LLMTaskPlanner


def get_task_planner():
    if get_task_planner_type() == "crewai":
        return CrewAITaskPlanner()
    return LLMTaskPlanner()
