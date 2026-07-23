import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select

from app.core.models import Agent, AgentCreate, WorkflowTemplate, utc_now
from app.services.storage import agents_table, metadata, workflow_templates_table
import scripts.seed_bugfix_workflow as bugfix_seed
from scripts.seed_bugfix_workflow import (
    BUGFIX_AGENT_SEEDS,
    BUGFIX_WORKFLOW_CREATE,
    BUGFIX_WORKFLOW_ID,
    MOCK_RELEASE_AGENT_ID,
    MOCK_RELEASE_AGENT_SEED,
    REQUIRED_LIFECYCLE_AGENT_IDS,
    main,
    seed_bugfix_workflow,
)


def test_bugfix_agent_seeds_reuse_lifecycle_agents_and_add_release_executor() -> None:
    agent_ids = [str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS]

    assert agent_ids == [
        *REQUIRED_LIFECYCLE_AGENT_IDS,
        MOCK_RELEASE_AGENT_ID,
    ]
    assert len(agent_ids) == len(set(agent_ids)) == 6

    mock_release = next(
        seed for seed in BUGFIX_AGENT_SEEDS if seed["id"] == MOCK_RELEASE_AGENT_ID
    )
    assert mock_release["name"] == "Mock 发布执行 Agent"
    assert mock_release["agent_type"] == "processing"
    assert mock_release["capabilities"] == ["release_execution", "mock_release"]
    assert mock_release["tools"] == []
    assert "不调用真实发布接口" in mock_release["execution_config"]["system_prompt"]


def test_bugfix_workflow_seed_describes_balanced_mock_pipeline() -> None:
    definition = BUGFIX_WORKFLOW_CREATE.definition
    node_ids = [node.id for node in definition.nodes]
    edge_pairs = [(edge.from_node, edge.to_node) for edge in definition.edges]

    assert BUGFIX_WORKFLOW_ID == "workflow_bugfix_demo"
    assert node_ids == [
        "start",
        "defect_analysis",
        "bug_fix_human",
        "code_review",
        "regression_test",
        "qa_gate_human",
        "deployment_check",
        "mock_release",
        "post_release_observation",
        "end",
    ]
    assert edge_pairs == [
        ("start", "defect_analysis"),
        ("defect_analysis", "bug_fix_human"),
        ("bug_fix_human", "code_review"),
        ("bug_fix_human", "regression_test"),
        ("code_review", "qa_gate_human"),
        ("regression_test", "qa_gate_human"),
        ("qa_gate_human", "deployment_check"),
        ("deployment_check", "mock_release"),
        ("mock_release", "post_release_observation"),
        ("post_release_observation", "end"),
    ]
    assert len(definition.nodes) == 10
    assert len(definition.edges) == 10
    assert not any(node.type == "condition" for node in definition.nodes)

    qa_edge = next(edge for edge in definition.edges if edge.from_node == "qa_gate_human")
    assert qa_edge.condition == {
        "field": "decision",
        "operator": "eq",
        "value": "approved",
    }

    agent_ids = {
        node.agent_id
        for node in definition.nodes
        if node.type == "agent"
    }
    assert agent_ids == {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}


def test_default_file_registry_includes_bugfix_workflow_and_agents() -> None:
    project_root = Path(__file__).resolve().parents[1]
    stored_agents = [
        Agent.model_validate(item)
        for item in json.loads(
            (project_root / "app/data/agents.json").read_text(encoding="utf-8")
        )
    ]
    stored_workflows = [
        WorkflowTemplate.model_validate(item)
        for item in json.loads(
            (project_root / "app/data/workflows.json").read_text(encoding="utf-8")
        )
    ]
    target_agent_ids = {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}
    bugfix_workflow = next(
        workflow
        for workflow in stored_workflows
        if workflow.id == BUGFIX_WORKFLOW_ID
    )

    assert target_agent_ids.issubset({agent.id for agent in stored_agents})
    assert bugfix_workflow.name == "Bug 修复演示闭环"
    assert bugfix_workflow.definition == BUGFIX_WORKFLOW_CREATE.definition


def _write_json_array(file_path, items: list[dict]) -> None:
    file_path.write_text(
        json.dumps(items, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_seed_bugfix_workflow_files_creates_missing_records_and_is_idempotent(
    tmp_path,
) -> None:
    agent_file = tmp_path / "agents.json"
    workflow_file = tmp_path / "workflows.json"
    _write_json_array(agent_file, [])
    _write_json_array(workflow_file, [])

    first_agents, first_workflow = bugfix_seed.seed_bugfix_workflow_files(
        agent_file,
        workflow_file,
    )
    second_agents, second_workflow = bugfix_seed.seed_bugfix_workflow_files(
        agent_file,
        workflow_file,
    )

    stored_agents = [
        Agent.model_validate(item)
        for item in json.loads(agent_file.read_text(encoding="utf-8"))
    ]
    stored_workflows = [
        WorkflowTemplate.model_validate(item)
        for item in json.loads(workflow_file.read_text(encoding="utf-8"))
    ]
    target_agent_ids = {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}

    assert {agent.id for agent in first_agents} == target_agent_ids
    assert {agent.id for agent in second_agents} == target_agent_ids
    assert first_workflow.id == second_workflow.id == BUGFIX_WORKFLOW_ID
    assert {agent.id for agent in stored_agents} == target_agent_ids
    assert [workflow.id for workflow in stored_workflows] == [BUGFIX_WORKFLOW_ID]


def test_seed_bugfix_workflow_files_preserves_unrelated_and_custom_records(
    tmp_path,
) -> None:
    now = utc_now()
    agent_file = tmp_path / "agents.json"
    workflow_file = tmp_path / "workflows.json"
    custom_defect_agent = Agent(
        id="agent_defect_analysis",
        name="自定义缺陷定位 Agent",
        description="保留文件中的用户配置",
        agent_type="processing",
        capabilities=["custom_defect_analysis"],
        created_at=now,
    )
    stale_mock_release = Agent(
        id=MOCK_RELEASE_AGENT_ID,
        name="旧发布 Agent",
        description="旧定义",
        agent_type="processing",
        capabilities=["legacy_release"],
        created_at=now,
    )
    unrelated_agent = Agent(
        id="agent_unrelated",
        name="无关 Agent",
        description="不得删除",
        agent_type="processing",
        created_at=now,
    )
    unrelated_workflow = WorkflowTemplate(
        id="workflow_unrelated",
        name="无关模板",
        description="不得删除",
        definition={"nodes": [], "edges": []},
        created_at=now,
        updated_at=now,
    )
    _write_json_array(
        agent_file,
        [
            custom_defect_agent.model_dump(mode="json"),
            stale_mock_release.model_dump(mode="json"),
            unrelated_agent.model_dump(mode="json"),
        ],
    )
    _write_json_array(
        workflow_file,
        [unrelated_workflow.model_dump(mode="json", by_alias=True)],
    )

    bugfix_seed.seed_bugfix_workflow_files(agent_file, workflow_file)

    stored_agents = {
        item["id"]: Agent.model_validate(item)
        for item in json.loads(agent_file.read_text(encoding="utf-8"))
    }
    stored_workflows = {
        item["id"]: WorkflowTemplate.model_validate(item)
        for item in json.loads(workflow_file.read_text(encoding="utf-8"))
    }
    expected_mock_release = Agent(
        id=MOCK_RELEASE_AGENT_ID,
        created_at=stored_agents[MOCK_RELEASE_AGENT_ID].created_at,
        **AgentCreate.model_validate(MOCK_RELEASE_AGENT_SEED).model_dump(),
    )

    assert stored_agents["agent_defect_analysis"] == custom_defect_agent
    assert stored_agents["agent_unrelated"] == unrelated_agent
    assert stored_agents[MOCK_RELEASE_AGENT_ID] == expected_mock_release
    assert stored_workflows["workflow_unrelated"] == unrelated_workflow
    assert stored_workflows[BUGFIX_WORKFLOW_ID].definition == (
        BUGFIX_WORKFLOW_CREATE.definition
    )


def _agent_row(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "payload": agent.model_dump_json(),
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "status": "active",
        "created_at": agent.created_at,
        "updated_at": agent.created_at,
    }


def test_seed_bugfix_workflow_creates_all_missing_records(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty-taskhub.db'}"

    seeded_agents, seeded_workflow = seed_bugfix_workflow(database_url)

    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        agent_rows = connection.execute(select(agents_table)).mappings().all()
        workflow_rows = connection.execute(
            select(workflow_templates_table)
        ).mappings().all()

    agents_by_id = {
        row["id"]: Agent.model_validate_json(row["payload"])
        for row in agent_rows
    }
    workflow_row = next(
        row for row in workflow_rows if row["id"] == BUGFIX_WORKFLOW_ID
    )
    target_agent_ids = {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}

    assert {agent.id for agent in seeded_agents} == target_agent_ids
    assert set(agents_by_id) == target_agent_ids
    assert seeded_workflow.id == BUGFIX_WORKFLOW_ID
    assert len(workflow_rows) == 1
    assert json.loads(workflow_row["definition_json"]) == (
        BUGFIX_WORKFLOW_CREATE.definition.model_dump(mode="json", by_alias=True)
    )


def test_seed_bugfix_workflow_migrates_legacy_agent_table(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-taskhub.db'}"
    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE agents (id VARCHAR(64) PRIMARY KEY, payload TEXT NULL)"
        )

    seeded_agents, seeded_workflow = seed_bugfix_workflow(database_url)

    agent_columns = {column["name"] for column in inspect(engine).get_columns("agents")}
    assert {"name", "capabilities_json", "execution_config_json", "updated_at"}.issubset(
        agent_columns
    )
    assert len(seeded_agents) == 6
    assert seeded_workflow.id == BUGFIX_WORKFLOW_ID


def test_seed_bugfix_workflow_is_idempotent_and_preserves_unrelated_data(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    engine = create_engine(database_url, future=True)
    metadata.create_all(engine)
    now = utc_now()
    custom_defect_agent = Agent(
        id="agent_defect_analysis",
        name="自定义缺陷定位 Agent",
        description="保留现有自定义配置",
        agent_type="processing",
        capabilities=["custom_defect_analysis"],
        created_at=now,
    )
    stale_mock_release = Agent(
        id=MOCK_RELEASE_AGENT_ID,
        name="旧发布 Agent",
        description="旧定义",
        agent_type="processing",
        capabilities=["legacy_release"],
        created_at=now,
    )
    unrelated_agent = Agent(
        id="agent_unrelated",
        name="无关 Agent",
        description="不得删除",
        agent_type="processing",
        created_at=now,
    )
    with engine.begin() as connection:
        connection.execute(
            agents_table.insert(),
            [
                _agent_row(custom_defect_agent),
                _agent_row(stale_mock_release),
                _agent_row(unrelated_agent),
            ],
        )
        connection.execute(
            workflow_templates_table.insert(),
            [
                {
                    "id": BUGFIX_WORKFLOW_ID,
                    "name": "旧 Bug 模板",
                    "description": "旧定义",
                    "definition_json": json.dumps({"nodes": [], "edges": []}),
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "workflow_unrelated",
                    "name": "无关模板",
                    "description": "不得删除",
                    "definition_json": json.dumps({"nodes": [], "edges": []}),
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    first_agents, first_workflow = seed_bugfix_workflow(database_url)
    second_agents, second_workflow = seed_bugfix_workflow(database_url)

    with engine.begin() as connection:
        agent_rows = connection.execute(select(agents_table)).mappings().all()
        workflow_rows = connection.execute(
            select(workflow_templates_table)
        ).mappings().all()

    agents_by_id = {
        row["id"]: Agent.model_validate_json(row["payload"])
        for row in agent_rows
    }
    workflows_by_id = {row["id"]: row for row in workflow_rows}
    target_agent_ids = {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}
    expected_mock_release = Agent(
        id=MOCK_RELEASE_AGENT_ID,
        created_at=agents_by_id[MOCK_RELEASE_AGENT_ID].created_at,
        **AgentCreate.model_validate(MOCK_RELEASE_AGENT_SEED).model_dump(),
    )

    assert {agent.id for agent in first_agents} == target_agent_ids
    assert {agent.id for agent in second_agents} == target_agent_ids
    assert first_workflow.id == second_workflow.id == BUGFIX_WORKFLOW_ID
    assert target_agent_ids.issubset(agents_by_id)
    assert agents_by_id["agent_defect_analysis"].model_dump(mode="json") == (
        custom_defect_agent.model_dump(mode="json")
    )
    assert agents_by_id[MOCK_RELEASE_AGENT_ID].model_dump(mode="json") == (
        expected_mock_release.model_dump(mode="json")
    )
    assert agents_by_id["agent_unrelated"].model_dump(mode="json") == (
        unrelated_agent.model_dump(mode="json")
    )
    assert workflows_by_id[BUGFIX_WORKFLOW_ID]["name"] == "Bug 修复演示闭环"
    assert workflows_by_id["workflow_unrelated"]["name"] == "无关模板"
    assert workflows_by_id["workflow_unrelated"]["description"] == "不得删除"

    definition = json.loads(workflows_by_id[BUGFIX_WORKFLOW_ID]["definition_json"])
    assert definition == BUGFIX_WORKFLOW_CREATE.definition.model_dump(
        mode="json",
        by_alias=True,
    )
    assert json.loads(workflows_by_id["workflow_unrelated"]["definition_json"]) == {
        "nodes": [],
        "edges": [],
    }
    assert len([row for row in agent_rows if row["id"] in target_agent_ids]) == 6
    assert len(
        [row for row in workflow_rows if row["id"] == BUGFIX_WORKFLOW_ID]
    ) == 1


def test_seed_cli_requires_url_and_does_not_echo_configured_url(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(bugfix_seed, "DEFAULT_DATABASE_URL", None)
    monkeypatch.setattr(sys, "argv", ["seed_bugfix_workflow"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    missing_url_output = capsys.readouterr()
    assert (
        "--database-url/DATABASE_URL or both --agent-file and --workflow-file are required"
        in missing_url_output.err
    )

    database_url = f"sqlite:///{tmp_path / 'cli-taskhub.db'}"
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_bugfix_workflow", "--database-url", database_url],
    )

    main()

    success_output = capsys.readouterr()
    assert database_url not in success_output.out
    assert database_url not in success_output.err
    assert BUGFIX_WORKFLOW_ID in success_output.out


def test_seed_cli_rejects_incomplete_or_explicitly_combined_file_mode(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(bugfix_seed, "DEFAULT_DATABASE_URL", None)

    invalid_argv = [
        ["seed_bugfix_workflow", "--agent-file", str(tmp_path / "agents.json")],
        [
            "seed_bugfix_workflow",
            "--database-url",
            f"sqlite:///{tmp_path / 'taskhub.db'}",
            "--agent-file",
            str(tmp_path / "agents.json"),
            "--workflow-file",
            str(tmp_path / "workflows.json"),
        ],
    ]
    for argv in invalid_argv:
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        capsys.readouterr()


def test_seed_cli_installs_file_registry_even_when_database_env_exists(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    agent_file = tmp_path / "agents.json"
    workflow_file = tmp_path / "workflows.json"
    monkeypatch.setenv("DATABASE_URL", "invalid://must-not-be-used")
    monkeypatch.setattr(bugfix_seed, "DEFAULT_DATABASE_URL", None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seed_bugfix_workflow",
            "--agent-file",
            str(agent_file),
            "--workflow-file",
            str(workflow_file),
        ],
    )

    main()

    output = capsys.readouterr()
    assert BUGFIX_WORKFLOW_ID in output.out
    assert {item["id"] for item in json.loads(agent_file.read_text())} == {
        str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS
    }
    assert [item["id"] for item in json.loads(workflow_file.read_text())] == [
        BUGFIX_WORKFLOW_ID
    ]
