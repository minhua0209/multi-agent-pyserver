from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_simple_agent_from_file_writing_ability(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/simple",
        json={
            "ability": "帮我创建一个可以向指定目录写入文章或者报告总结的agent",
            "name": "报告写入助手",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["agent"]["name"] == "报告写入助手"
    assert body["agent"]["agent_type"] == "processing"
    assert "write_report" in body["agent"]["capabilities"]
    assert body["agent"]["tools"][0]["type"] == "file_write"
    assert "config" not in body["agent"]["tools"][0]
    assert body["matched_tools"] == ["file_write"]
    assert body["missing_tools"] == []


def test_create_simple_agent_rejects_too_many_abilities(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/simple",
        json={
            "ability": "帮我创建一个agent，既能查询MySQL客户数据，又能发送邮件，还能调用外部HTTP接口",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_split"
    assert body["agent"] is None
    assert "分开创建" in body["message"]
    assert client.get("/api/v1/agents").json() == []


def test_create_simple_agent_reports_missing_tool(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/simple",
        json={"ability": "帮我创建一个agent，可以登录企业微信并把日报发送到群里"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "tool_missing"
    assert body["agent"] is None
    assert body["missing_tools"][0]["type"] == "wechat_group_sender"


def test_create_agent_persists_to_local_json_file(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles customer quote tasks",
            "capabilities": ["quote", "crm"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Quote Agent"
    assert body["capabilities"] == ["quote", "crm"]
    assert body["agent_type"] == "processing"

    list_response = client.get("/api/v1/agents")
    assert list_response.status_code == 200
    assert list_response.json() == [body]
    assert "Quote Agent" in data_file.read_text()


def test_delete_agent_removes_it_from_registry(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    agent = client.post(
        "/api/v1/agents",
        json={"name": "Temporary Agent", "description": "Can be deleted", "capabilities": ["temporary"]},
    ).json()

    response = client.delete(f"/api/v1/agents/{agent['id']}")

    assert response.status_code == 204
    assert client.get("/api/v1/agents").json() == []


def test_delete_agent_is_blocked_when_workflow_references_it(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json", workflow_file=tmp_path / "workflows.json"))
    agent = client.post(
        "/api/v1/agents",
        json={"name": "Referenced Agent", "description": "Used by workflow", "capabilities": ["workflow"]},
    ).json()
    client.post(
        "/api/v1/workflows",
        json={
            "name": "Referenced Workflow",
            "definition": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "work", "type": "agent", "agent_id": agent["id"]},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"from": "start", "to": "work"},
                    {"from": "work", "to": "end"},
                ],
            },
        },
    )

    response = client.delete(f"/api/v1/agents/{agent['id']}")

    assert response.status_code == 409
    assert "Referenced Workflow" in response.json()["detail"]
    assert client.get("/api/v1/agents").json()[0]["id"] == agent["id"]


def test_create_agent_accepts_tool_definitions(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "CRM Agent",
            "description": "Uses CRM tools",
            "capabilities": ["crm"],
            "tools": [
                {
                    "name": "crm_query",
                    "description": "Query customer information from CRM",
                    "type": "http",
                    "config": {
                        "method": "GET",
                        "url": "https://crm.example.com/customers/{customer_id}",
                    },
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "customer_id": {"type": "string"},
                        },
                    },
                }
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["tools"][0]["name"] == "crm_query"
    assert body["tools"][0]["type"] == "http"
    assert body["tools"][0]["input_schema"]["properties"]["customer_id"]["type"] == "string"
    assert "config" not in body["tools"][0]
    assert client.app.state.agent_registry.list_agents()[0].tools[0].config["method"] == "GET"


def test_create_agent_accepts_agent_type(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Condition Judge Agent",
            "description": "Normalizes workflow decisions",
            "agent_type": "condition",
            "capabilities": ["decision"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["agent_type"] == "condition"


def test_create_human_agent_persists_default_assignee_metadata(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "报价人工审批节点",
            "description": "负责报价类人工审批",
            "agent_type": "human",
            "capabilities": ["quote_approval"],
            "metadata": {
                "assignee_user_id": "user_001",
                "assignee_user_name": "张三",
                "assignee_role": "quote_approver",
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["agent_type"] == "human"
    assert "metadata" not in body
    internal = client.app.state.agent_registry.list_agents()[0]
    assert internal.metadata["assignee_user_id"] == "user_001"
    assert internal.metadata["assignee_user_name"] == "张三"


def test_create_human_node_persists_explicit_assignee_name(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/human-node",
        json={
            "name": "报价审批节点",
            "assignee_user_name": "王大锤",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["agent"]["agent_type"] == "human"
    assert body["agent"]["description"] == "人工审批节点，审批人：王大锤"
    assert "metadata" not in body["agent"]
    assert "metadata" not in client.get("/api/v1/agents").json()[0]
    internal = client.app.state.agent_registry.list_agents()[0]
    assert internal.metadata["assignee_user_id"] == "王大锤"
    assert internal.metadata["assignee_user_name"] == "王大锤"
    assert internal.metadata["assignee_role"] == "approver"


def test_create_human_node_accepts_custom_assignee_role(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/human-node",
        json={
            "name": "通用审批节点",
            "assignee_user_name": "王大锤",
            "assignee_role": "quote_approver",
        },
    )

    assert response.status_code == 201
    assert "metadata" not in response.json()["agent"]
    metadata = client.app.state.agent_registry.list_agents()[0].metadata
    assert metadata["assignee_user_id"] == "王大锤"
    assert metadata["assignee_user_name"] == "王大锤"
    assert metadata["assignee_role"] == "quote_approver"


def test_create_human_node_rejects_when_assignee_name_missing(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents/human-node",
        json={
            "name": "通用审批节点",
            "assignee_user_name": "",
        },
    )

    assert response.status_code == 422
    assert client.get("/api/v1/agents").json() == []


def test_create_agent_accepts_execution_config_and_io_schema(tmp_path: Path) -> None:
    data_file = tmp_path / "agents.json"
    client = TestClient(create_app(agent_file=data_file))

    response = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Uses a custom model and structured IO",
            "capabilities": ["quote"],
            "input_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
            },
            "output_schema": {
                "type": "object",
                "properties": {"quote_amount": {"type": "number"}},
            },
            "execution_config": {
                "system_prompt": "你是报价 agent",
                "model_name": "qwen3.6-35b",
                "temperature": 0.2,
                "timeout_seconds": 30,
                "max_retries": 2,
                "max_tool_calls": 3,
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["input_schema"]["properties"]["customer_id"]["type"] == "string"
    assert body["output_schema"]["properties"]["quote_amount"]["type"] == "number"
    assert "execution_config" not in body
    internal = client.app.state.agent_registry.list_agents()[0]
    assert internal.execution_config.system_prompt == "你是报价 agent"
    assert internal.execution_config.model_name == "qwen3.6-35b"
    assert internal.execution_config.max_tool_calls == 3
