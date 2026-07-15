import json
import sys
import types

from app.core.models import Agent, utc_now
from app.planners.base import round_plan_from_dict
from app.planners.crewai_planner import CrewAITaskPlanner
from app.planners.factory import get_task_planner


def test_round_plan_from_dict_preserves_human_and_valid_agent_ids() -> None:
    agent = Agent(id="agent_quote", name="Quote Agent", capabilities=["quote"], created_at=utc_now())

    plan = round_plan_from_dict(
        {
            "should_continue": True,
            "execution_mode": "sequential",
            "reason": "Need approval then quote",
            "subtasks": [
                {
                    "title": "Approve plan",
                    "description": "Human approves plan",
                    "assignee_type": "human",
                    "assigned_agent_id": "agent_quote",
                },
                {
                    "title": "Create quote",
                    "description": "Create quote after approval",
                    "assignee_type": "agent",
                    "assigned_agent_id": "agent_quote",
                },
                {
                    "title": "Invalid agent task",
                    "description": "Invalid agent id should be dropped",
                    "assignee_type": "agent",
                    "assigned_agent_id": "missing",
                },
            ],
        },
        [agent],
    )

    assert plan.execution_mode == "sequential"
    assert plan.subtasks[0].assignee_type == "human"
    assert plan.subtasks[0].assigned_agent_id == "agent_quote"
    assert plan.subtasks[1].assigned_agent_id == "agent_quote"
    assert plan.subtasks[2].assigned_agent_id is None


def test_task_planner_factory_uses_crewai_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("TASK_PLANNER_TYPE", "crewai")

    assert isinstance(get_task_planner(), CrewAITaskPlanner)


def test_crewai_planner_parses_crew_output(monkeypatch) -> None:
    class FakeCrewAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeCrewTask:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeLLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeCrew:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def kickoff(self):
            return json.dumps(
                {
                    "should_continue": True,
                    "execution_mode": "parallel",
                    "reason": "Need agent work and human approval",
                    "subtasks": [
                        {
                            "title": "Prepare quote",
                            "description": "Prepare quote draft",
                            "assignee_type": "agent",
                            "assigned_agent_id": "agent_quote",
                        },
                        {
                            "title": "Approve quote",
                            "description": "Human approves quote",
                            "assignee_type": "human",
                            "assigned_agent_id": None,
                        },
                    ],
                }
            )

    fake_module = types.ModuleType("crewai")
    fake_module.Agent = FakeCrewAgent
    fake_module.Crew = FakeCrew
    fake_module.LLM = FakeLLM
    fake_module.Process = types.SimpleNamespace(sequential="sequential")
    fake_module.Task = FakeCrewTask
    monkeypatch.setitem(sys.modules, "crewai", fake_module)

    agent = Agent(id="agent_quote", name="Quote Agent", capabilities=["quote"], created_at=utc_now())
    plan = CrewAITaskPlanner().plan_next_round(
        task=types.SimpleNamespace(
            id="task_1",
            title="Create quote",
            description="Create quote and approve",
            content="Create quote and approve",
            loop_count=0,
            max_loop_count=10,
            context=types.SimpleNamespace(model_dump=lambda mode: {}),
        ),
        agents=[agent],
    )

    assert plan is not None
    assert plan.execution_mode == "parallel"
    assert plan.subtasks[0].assigned_agent_id == "agent_quote"
    assert plan.subtasks[1].assignee_type == "human"


def test_crewai_planner_instructs_chinese_user_facing_output() -> None:
    description = CrewAITaskPlanner._build_task_description(
        task=types.SimpleNamespace(
            id="task_1",
            title="Create quote",
            description="Create quote and approve",
            content="Create quote and approve",
            loop_count=0,
            max_loop_count=10,
            context=types.SimpleNamespace(model_dump=lambda mode: {}),
        ),
        agents=[],
    )

    assert "Write all user-facing text fields in Chinese" in description
    assert "reason, final_output, subtasks.title, subtasks.description" in description
