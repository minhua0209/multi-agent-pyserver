import json
import sys
import types

from app.core.enums import CurrentNode, SourceType, TaskStatus
from app.core.models import Agent, AgentTool, Task, TaskContract, TaskContractItem, utc_now
from app.planners.base import round_plan_from_dict
from app.planners.crewai_planner import CrewAITaskPlanner
from app.planners.factory import get_task_planner
from app.planners.llm_planner import LLMTaskPlanner


def _planner_task() -> Task:
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        title="Create quote",
        description="Create quote and approve",
        content="Create quote and approve",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=TaskContract(
            goal="Create an approved quote",
            deliverable_goal="Deliver a reviewable quote",
            success_criteria=[
                TaskContractItem(id="criterion_reviewable", description="Quote is reviewable")
            ],
            confirmed_at=utc_now(),
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
    )


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


def test_llm_planner_includes_contract_payload(monkeypatch) -> None:
    captured = {}
    task = _planner_task()

    def fake_create(system_prompt: str, user_prompt: str) -> str:
        captured["user_prompt"] = user_prompt
        return '{"should_continue": false, "final_output": "完成", "subtasks": []}'

    monkeypatch.setattr("app.planners.llm_planner.default_client.create", fake_create)

    plan = LLMTaskPlanner().plan_next_round(task, [])

    assert plan is not None
    payload = json.loads(captured["user_prompt"])
    assert payload["task"]["contract"] == task.contract.model_dump(mode="json")


def test_crewai_planner_includes_contract_payload_and_authoritative_instruction() -> None:
    task = _planner_task()

    payload = json.loads(CrewAITaskPlanner._build_task_description(task, []))

    assert payload["task"]["contract"] == task.contract.model_dump(mode="json")
    assert "Treat the confirmed task contract as the authoritative execution basis." in payload[
        "instructions"
    ]


def test_planners_do_not_send_agent_tool_configuration(monkeypatch) -> None:
    sensitive_value = "planner-secret-placeholder"
    agent = Agent(
        id="agent_secure",
        name="Secure Agent",
        description="Uses a credentialed tool",
        capabilities=["secure_lookup"],
        tools=[
            AgentTool(
                name="secure_lookup",
                description="Lookup private data",
                type="mysql",
                config={"password": sensitive_value, "host": "private-db"},
                input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            )
        ],
        created_at=utc_now(),
    )
    captured = {}

    def fake_create(_system_prompt: str, user_prompt: str) -> str:
        captured["user_prompt"] = user_prompt
        return '{"should_continue": false, "final_output": "完成", "subtasks": []}'

    monkeypatch.setattr("app.planners.llm_planner.default_client.create", fake_create)

    assert LLMTaskPlanner().plan_next_round(_planner_task(), [agent]) is not None
    llm_payload = json.loads(captured["user_prompt"])
    crewai_payload = json.loads(CrewAITaskPlanner._build_task_description(_planner_task(), [agent]))

    expected_tool = {
        "name": "secure_lookup",
        "description": "Lookup private data",
        "type": "mysql",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
    }
    for payload in (llm_payload, crewai_payload):
        tool_payload = payload["available_agents"][0]["tools"][0]
        assert tool_payload == expected_tool
        assert sensitive_value not in json.dumps(payload)
        assert "config" not in tool_payload


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
            contract=None,
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
            contract=None,
            context=types.SimpleNamespace(model_dump=lambda mode: {}),
        ),
        agents=[],
    )

    assert "Write all user-facing text fields in Chinese" in description
    assert "reason, final_output, subtasks.title, subtasks.description" in description
