from app.core.models import Agent, AgentTool, ToolCall, utc_now
from app.services.tool_executor import ToolExecutor


def test_tool_executor_runs_mock_tool() -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        tools=[
            AgentTool(
                name="crm_query",
                description="Query CRM",
                type="mock",
                config={"response": '{"customer_name": "Customer A", "level": "vip"}'},
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"}),
    )

    assert result.success is True
    assert result.tool_name == "crm_query"
    assert result.result == '{"customer_name": "Customer A", "level": "vip"}'


def test_tool_executor_rejects_unregistered_tool() -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"}),
    )

    assert result.success is False
    assert "not registered" in result.error
