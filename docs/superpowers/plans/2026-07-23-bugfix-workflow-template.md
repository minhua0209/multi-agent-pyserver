# Bug 修复演示 Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一条可幂等安装、QA 驳回即阻塞的 Bug 修复 Mock 演示 Workflow，并补齐完整通过与驳回链路测试。

**Architecture:** 使用独立种子脚本定义六个 Processing Agent 和固定 ID 的无环 Workflow。模板复用现有执行器的并行 ready-node 调度与人工节点 `result_metadata` 条件边，不修改通用 Workflow 引擎或前端。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、SQLite 测试数据库。

**Repository Constraint:** 按仓库 `AGENTS.md`，本计划不包含 Git 暂存或提交步骤。

---

## File Map

- Create `scripts/seed_bugfix_workflow.py`: Bug 修复演示 Agent、Workflow 定义和非破坏性数据库种子入口。
- Create `tests/test_seed_bugfix_workflow.py`: 定义完整性、幂等更新和无关数据保留测试。
- Modify `tests/test_workflows.py`: 通过与 QA 驳回两条端到端 API 执行链路。
- Modify `docs/test-cases/Bug修复流程场景说明.md`: 与新模板一致的安装、节点、决策和验证说明。

### Task 1: Define The Bugfix Demo Agents

**Files:**

- Create: `tests/test_seed_bugfix_workflow.py`
- Create: `scripts/seed_bugfix_workflow.py`

- [ ] **Step 1: Write the failing Agent seed test**

Create `tests/test_seed_bugfix_workflow.py` with:

```python
from scripts.seed_bugfix_workflow import (
    BUGFIX_AGENT_SEEDS,
    MOCK_RELEASE_AGENT_ID,
    REQUIRED_LIFECYCLE_AGENT_IDS,
)


def test_bugfix_agent_seeds_reuse_lifecycle_agents_and_add_release_executor() -> None:
    agent_ids = [str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS]

    assert agent_ids == [
        *REQUIRED_LIFECYCLE_AGENT_IDS,
        MOCK_RELEASE_AGENT_ID,
    ]
    assert len(agent_ids) == len(set(agent_ids)) == 6

    mock_release = next(seed for seed in BUGFIX_AGENT_SEEDS if seed["id"] == MOCK_RELEASE_AGENT_ID)
    assert mock_release["name"] == "Mock 发布执行 Agent"
    assert mock_release["agent_type"] == "processing"
    assert mock_release["capabilities"] == ["release_execution", "mock_release"]
    assert mock_release["tools"] == []
    assert "不调用真实发布接口" in mock_release["execution_config"]["system_prompt"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py::test_bugfix_agent_seeds_reuse_lifecycle_agents_and_add_release_executor
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scripts.seed_bugfix_workflow'`.

- [ ] **Step 3: Add the minimal Agent seed definitions**

Create `scripts/seed_bugfix_workflow.py` with:

```python
from __future__ import annotations

from typing import Any

from scripts.seed_lifecycle_agents import LIFECYCLE_AGENT_SEEDS


REQUIRED_LIFECYCLE_AGENT_IDS = [
    "agent_defect_analysis",
    "agent_code_review",
    "agent_automation_testing",
    "agent_deployment_check",
    "agent_monitoring_alerting",
]
MOCK_RELEASE_AGENT_ID = "agent_mock_release_execution"

_LIFECYCLE_AGENT_BY_ID = {
    str(seed["id"]): seed
    for seed in LIFECYCLE_AGENT_SEEDS
}

MOCK_RELEASE_AGENT_SEED: dict[str, Any] = {
    "id": MOCK_RELEASE_AGENT_ID,
    "name": "Mock 发布执行 Agent",
    "description": "根据上线检查结果模拟发布版本、批次、时间和发布状态。",
    "agent_type": "processing",
    "capabilities": ["release_execution", "mock_release"],
    "input_schema": {
        "context_inputs": ["task.content", "context.summary", "subtask.output"],
        "required": ["任务目标", "上线检查结果"],
    },
    "output_schema": {
        "context_outputs": ["release.version", "release.batch", "release.status"],
        "required": ["发布版本", "发布批次", "发布时间", "发布状态", "观察建议"],
    },
    "execution_config": {
        "system_prompt": (
            "你是 Mock 发布执行 Agent。请根据上线检查结果生成结构化的模拟发布记录，"
            "包含发布版本、发布批次、发布时间、发布状态和观察建议。"
            "只生成 Mock 结果，不调用真实发布接口。"
        ),
        "timeout_seconds": 90,
        "max_retries": 1,
        "max_tool_calls": 0,
    },
    "tools": [],
    "metadata": {
        "stage": "上线",
        "icon": "Rocket",
        "seed_version": "2026-07-23-bugfix-workflow",
    },
}

BUGFIX_AGENT_SEEDS = [
    *[_LIFECYCLE_AGENT_BY_ID[agent_id] for agent_id in REQUIRED_LIFECYCLE_AGENT_IDS],
    MOCK_RELEASE_AGENT_SEED,
]
```

- [ ] **Step 4: Run the Agent seed test and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py::test_bugfix_agent_seeds_reuse_lifecycle_agents_and_add_release_executor
```

Expected: PASS.

### Task 2: Define And Execute The Balanced Workflow

**Files:**

- Modify: `scripts/seed_bugfix_workflow.py`
- Modify: `tests/test_seed_bugfix_workflow.py`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Add the failing Workflow structure test**

Extend the import in `tests/test_seed_bugfix_workflow.py`:

```python
from scripts.seed_bugfix_workflow import (
    BUGFIX_AGENT_SEEDS,
    BUGFIX_WORKFLOW_CREATE,
    BUGFIX_WORKFLOW_ID,
    MOCK_RELEASE_AGENT_ID,
    REQUIRED_LIFECYCLE_AGENT_IDS,
)
```

Append:

```python
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
```

- [ ] **Step 2: Add failing happy-path and QA-rejection integration tests**

In `tests/test_workflows.py`, add `Agent` to the existing `app.core.models` import and add:

```python
from scripts.seed_bugfix_workflow import BUGFIX_AGENT_SEEDS, BUGFIX_WORKFLOW_CREATE
```

Append these helpers and tests:

```python
def _bugfix_demo_client(tmp_path: Path) -> tuple[TestClient, dict]:
    now = utc_now()
    agents = [
        Agent(
            id=str(seed["id"]),
            created_at=now,
            **AgentCreate.model_validate(seed).model_dump(),
        )
        for seed in BUGFIX_AGENT_SEEDS
    ]
    agent_file = tmp_path / "agents.json"
    agent_file.write_text(
        json.dumps(
            [agent.model_dump(mode="json") for agent in agents],
            ensure_ascii=False,
        )
    )
    client = TestClient(
        create_app(
            agent_file=agent_file,
            workflow_file=tmp_path / "workflows.json",
        )
    )
    response = client.post(
        "/api/v1/workflows",
        json=BUGFIX_WORKFLOW_CREATE.model_dump(mode="json", by_alias=True),
    )
    assert response.status_code == 201
    return client, response.json()


def _workflow_subtasks(task: dict) -> list[dict]:
    return [
        subtask
        for round_item in task["context"]["rounds"]
        for subtask in round_item["subtasks"]
    ]


def _running_human_subtask(task: dict, title: str) -> dict:
    return next(
        subtask
        for subtask in _workflow_subtasks(task)
        if subtask["title"] == title and subtask["status"] == "running"
    )


def _run_bugfix_demo_to_qa(tmp_path: Path, monkeypatch) -> tuple[TestClient, str, dict]:
    client, workflow = _bugfix_demo_client(tmp_path)
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda _task, subtask, _agent, _tool_results: (
            [],
            f"{subtask.title}完成：Mock 结论通过。",
        ),
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "登录状态失效 Bug 修复",
            "content": "模拟完成登录状态失效问题的分析、修复、测试和发布。",
            "metadata": {
                "execution_mode": "workflow_template",
                "workflow_id": workflow["id"],
            },
        },
    ).json()["tasks"][0]
    paused_for_fix = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "登录状态失效 Bug 修复",
            "description": "模拟完成登录状态失效问题的分析、修复、测试和发布。",
        },
    ).json()
    fix_subtask = _running_human_subtask(paused_for_fix, "人工模拟修复")
    paused_for_qa = client.post(
        f"/api/v1/subtasks/{fix_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "模拟修复完成：调整登录态刷新逻辑，自测通过。",
            "should_complete": False,
            "metadata": {},
        },
    ).json()
    return client, created["id"], paused_for_qa


def test_bugfix_demo_workflow_runs_two_human_gates_and_completes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, task_id, paused_for_qa = _run_bugfix_demo_to_qa(tmp_path, monkeypatch)

    parallel_round = next(
        round_item
        for round_item in paused_for_qa["context"]["rounds"]
        if {subtask["title"] for subtask in round_item["subtasks"]}
        == {"代码评审", "回归测试"}
    )
    assert parallel_round["execution_mode"] == "parallel"
    assert paused_for_qa["current_node"] == "human_execution"

    qa_subtask = _running_human_subtask(paused_for_qa, "QA 人工门禁")
    completed = client.post(
        f"/api/v1/subtasks/{qa_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "QA 审核通过，可以发布。",
            "should_complete": False,
            "metadata": {"decision": "approved"},
        },
    ).json()

    completed_titles = [
        subtask["title"]
        for subtask in _workflow_subtasks(completed)
        if subtask["status"] == "succeeded"
    ]
    assert completed["task_status"] == "succeeded"
    assert completed["completion_report"]["workflow_end_node_id"] == "end"
    assert completed_titles == [
        "缺陷复现与影响评估",
        "人工模拟修复",
        "代码评审",
        "回归测试",
        "QA 人工门禁",
        "上线前检查",
        "Mock 发布执行",
        "发布后观察",
    ]
    assert completed["id"] == task_id


def test_bugfix_demo_workflow_blocks_release_when_qa_rejects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _task_id, paused_for_qa = _run_bugfix_demo_to_qa(tmp_path, monkeypatch)
    qa_subtask = _running_human_subtask(paused_for_qa, "QA 人工门禁")

    blocked = client.post(
        f"/api/v1/subtasks/{qa_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "QA 驳回：回归测试证据不足。",
            "should_complete": False,
            "metadata": {"decision": "rejected"},
        },
    ).json()

    completed_titles = {
        subtask["title"]
        for subtask in _workflow_subtasks(blocked)
        if subtask["status"] == "succeeded"
    }
    assert blocked["task_status"] == "blocked"
    assert blocked["current_node"] == "completion_judge"
    assert blocked["completion_report"]["terminal_status"] == "blocked"
    assert blocked["completion_report"]["workflow_end_node_id"] is None
    assert "没有可继续执行的节点" in blocked["final_output"]
    assert completed_titles.isdisjoint({"上线前检查", "Mock 发布执行", "发布后观察"})
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_seed_bugfix_workflow.py::test_bugfix_workflow_seed_describes_balanced_mock_pipeline \
  tests/test_workflows.py::test_bugfix_demo_workflow_runs_two_human_gates_and_completes \
  tests/test_workflows.py::test_bugfix_demo_workflow_blocks_release_when_qa_rejects
```

Expected: FAIL during collection because `BUGFIX_WORKFLOW_CREATE` and `BUGFIX_WORKFLOW_ID` do not exist.

- [ ] **Step 4: Add the Workflow definition**

Add this import to `scripts/seed_bugfix_workflow.py`:

```python
from app.core.models import WorkflowCreate
```

Append:

```python
BUGFIX_WORKFLOW_ID = "workflow_bugfix_demo"

BUGFIX_WORKFLOW_CREATE = WorkflowCreate.model_validate(
    {
        "name": "Bug 修复演示闭环",
        "description": (
            "模拟完成缺陷分析、人工修复、代码评审、回归测试、QA 门禁、"
            "上线检查、发布和发布后观察。QA 仅在明确通过时进入发布阶段。"
        ),
        "definition": {
            "nodes": [
                {"id": "start", "type": "start", "title": "开始"},
                {
                    "id": "defect_analysis",
                    "type": "agent",
                    "title": "缺陷复现与影响评估",
                    "description": (
                        "模拟确认复现结果，并输出严重级别、影响模块、建议归属和风险。"
                    ),
                    "agent_id": "agent_defect_analysis",
                },
                {
                    "id": "bug_fix_human",
                    "type": "human",
                    "title": "人工模拟修复",
                    "description": "根据缺陷分析模拟完成修复并给出自测结果。",
                    "config": {
                        "assignee_user_id": "root",
                        "assignee_user_name": "管理员",
                        "assignee_role": "bug_fix_owner",
                        "handoff_instruction": (
                            "请根据缺陷复现与影响评估结果模拟完成 Bug 修复，并说明根因、"
                            "修改内容、影响范围和自测结果。"
                        ),
                    },
                },
                {
                    "id": "code_review",
                    "type": "agent",
                    "title": "代码评审",
                    "description": "模拟评审修复方案，输出质量问题、风险和上线阻塞项。",
                    "agent_id": "agent_code_review",
                },
                {
                    "id": "regression_test",
                    "type": "agent",
                    "title": "回归测试",
                    "description": "模拟执行目标用例和回归用例，输出数量、失败项和结论。",
                    "agent_id": "agent_automation_testing",
                },
                {
                    "id": "qa_gate_human",
                    "type": "human",
                    "title": "QA 人工门禁",
                    "description": "结合代码评审和回归测试结果决定是否允许发布。",
                    "config": {
                        "assignee_user_id": "root",
                        "assignee_user_name": "管理员",
                        "assignee_role": "qa_reviewer",
                        "required_metadata": ["decision"],
                        "handoff_instruction": (
                            "请结合代码评审和回归测试结果进行 QA 审核。通过时提交 "
                            "decision=approved；驳回时提交 decision=rejected；信息不足时提交 "
                            "decision=need_more_info。"
                        ),
                    },
                },
                {
                    "id": "deployment_check",
                    "type": "agent",
                    "title": "上线前检查",
                    "description": "模拟检查版本、配置、依赖、灰度、回滚和监控准备。",
                    "agent_id": "agent_deployment_check",
                },
                {
                    "id": "mock_release",
                    "type": "agent",
                    "title": "Mock 发布执行",
                    "description": "模拟生成发布版本、批次、时间和发布状态。",
                    "agent_id": MOCK_RELEASE_AGENT_ID,
                },
                {
                    "id": "post_release_observation",
                    "type": "agent",
                    "title": "发布后观察",
                    "description": "模拟观察核心指标、告警情况和发布结论。",
                    "agent_id": "agent_monitoring_alerting",
                },
                {"id": "end", "type": "end", "title": "完成"},
            ],
            "edges": [
                {"from": "start", "to": "defect_analysis"},
                {"from": "defect_analysis", "to": "bug_fix_human"},
                {"from": "bug_fix_human", "to": "code_review"},
                {"from": "bug_fix_human", "to": "regression_test"},
                {"from": "code_review", "to": "qa_gate_human"},
                {"from": "regression_test", "to": "qa_gate_human"},
                {
                    "from": "qa_gate_human",
                    "to": "deployment_check",
                    "condition": {
                        "field": "decision",
                        "operator": "eq",
                        "value": "approved",
                    },
                },
                {"from": "deployment_check", "to": "mock_release"},
                {"from": "mock_release", "to": "post_release_observation"},
                {"from": "post_release_observation", "to": "end"},
            ],
        },
    }
)
```

- [ ] **Step 5: Run the Workflow tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_seed_bugfix_workflow.py::test_bugfix_workflow_seed_describes_balanced_mock_pipeline \
  tests/test_workflows.py::test_bugfix_demo_workflow_runs_two_human_gates_and_completes \
  tests/test_workflows.py::test_bugfix_demo_workflow_blocks_release_when_qa_rejects
```

Expected: `3 passed`.

### Task 3: Add Non-Destructive Idempotent Database Seeding

**Files:**

- Modify: `tests/test_seed_bugfix_workflow.py`
- Modify: `scripts/seed_bugfix_workflow.py`

- [ ] **Step 1: Write the failing persistence and CLI tests**

Add these imports to `tests/test_seed_bugfix_workflow.py`:

```python
import json
import sys

import pytest
from sqlalchemy import create_engine, inspect, select

from app.core.models import Agent, AgentCreate, utc_now
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
```

Append:

```python
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
        workflow_rows = connection.execute(select(workflow_templates_table)).mappings().all()

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


def test_seed_bugfix_workflow_is_idempotent_and_preserves_unrelated_data(tmp_path) -> None:
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
        workflow_rows = connection.execute(select(workflow_templates_table)).mappings().all()

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
    assert len([row for row in workflow_rows if row["id"] == BUGFIX_WORKFLOW_ID]) == 1


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
    assert "--database-url or DATABASE_URL is required" in missing_url_output.err

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
```

- [ ] **Step 2: Run the new seed tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py
```

Expected: FAIL during collection because the persistence and CLI functions do not exist yet.

- [ ] **Step 3: Implement database upsert helpers and CLI**

Add these imports to `scripts/seed_bugfix_workflow.py`:

```python
import argparse
import os
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

from app.core.config import DEFAULT_DATABASE_URL
from app.core.models import Agent, AgentCreate, WorkflowTemplate, utc_now
from app.services.storage import (
    _json_dump,
    agents_table,
    workflow_templates_table,
)
```

Extend the existing lifecycle seed import:

```python
from scripts.seed_lifecycle_agents import LIFECYCLE_AGENT_SEEDS, _ensure_agents_schema
```

Append:

```python
def _ensure_schema(database_url: str) -> Engine:
    _ensure_agents_schema(database_url)
    return create_engine(database_url, future=True)


def _agent_from_seed(seed: dict[str, Any], created_at: datetime) -> Agent:
    payload = AgentCreate.model_validate(seed)
    return Agent(
        id=str(seed["id"]),
        created_at=created_at,
        **payload.model_dump(),
    )


def _agent_values(agent: Agent, updated_at: datetime) -> dict:
    return {
        "id": agent.id,
        "payload": agent.model_dump_json(),
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "capabilities_json": _json_dump(agent.capabilities),
        "input_schema_json": _json_dump(agent.input_schema),
        "output_schema_json": _json_dump(agent.output_schema),
        "execution_config_json": agent.execution_config.model_dump_json(),
        "tools_json": _json_dump([tool.model_dump(mode="json") for tool in agent.tools]),
        "metadata_json": _json_dump(agent.metadata),
        "status": "active",
        "created_at": agent.created_at,
        "updated_at": updated_at,
    }


def _workflow_values(workflow: WorkflowTemplate) -> dict:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "definition_json": workflow.definition.model_dump_json(by_alias=True),
        "status": workflow.status,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def seed_bugfix_workflow(database_url: str) -> tuple[list[Agent], WorkflowTemplate]:
    if not database_url:
        raise ValueError("Database URL is required to seed the Bugfix Workflow")

    engine = _ensure_schema(database_url)
    now = utc_now()
    seeded_agents: list[Agent] = []
    with engine.begin() as connection:
        for seed in BUGFIX_AGENT_SEEDS:
            agent_id = str(seed["id"])
            existing = connection.execute(
                select(agents_table).where(agents_table.c.id == agent_id)
            ).mappings().first()
            if existing is not None and agent_id != MOCK_RELEASE_AGENT_ID:
                seeded_agents.append(Agent.model_validate_json(existing["payload"]))
                continue

            created_at = existing["created_at"] if existing and existing["created_at"] else now
            agent = _agent_from_seed(seed, created_at)
            values = _agent_values(agent, now)
            if existing is None:
                connection.execute(agents_table.insert().values(**values))
            else:
                connection.execute(
                    agents_table.update()
                    .where(agents_table.c.id == agent_id)
                    .values(**values)
                )
            seeded_agents.append(agent)

        existing_workflow = connection.execute(
            select(workflow_templates_table).where(
                workflow_templates_table.c.id == BUGFIX_WORKFLOW_ID
            )
        ).mappings().first()
        workflow = WorkflowTemplate(
            id=BUGFIX_WORKFLOW_ID,
            status=str(existing_workflow["status"]) if existing_workflow else "active",
            created_at=(
                existing_workflow["created_at"]
                if existing_workflow and existing_workflow["created_at"]
                else now
            ),
            updated_at=now,
            **BUGFIX_WORKFLOW_CREATE.model_dump(by_alias=True),
        )
        values = _workflow_values(workflow)
        if existing_workflow is None:
            connection.execute(workflow_templates_table.insert().values(**values))
        else:
            connection.execute(
                workflow_templates_table.update()
                .where(workflow_templates_table.c.id == BUGFIX_WORKFLOW_ID)
                .values(**values)
            )

    return seeded_agents, workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the Bugfix demo Workflow and required Agents into the configured database."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL,
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    agents, workflow = seed_bugfix_workflow(args.database_url)
    print(f"Seeded {len(agents)} agents and workflow {workflow.id}.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the seed tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py
```

Expected: `6 passed`.

### Task 4: Replace The Stale Bugfix Scenario Documentation

**Files:**

- Modify: `docs/test-cases/Bug修复流程场景说明.md`

- [ ] **Step 1: Rewrite the scenario around the executable seed**

Replace the current document with sections in this exact order:

````markdown
# Bug 修复流程场景说明

## 场景目标

验证“缺陷分析、人工模拟修复、并行代码评审与回归测试、QA 人工门禁、上线检查、Mock 发布、发布后观察”的完整演示闭环。所有修复、测试和发布结果均为 Mock，不修改真实代码或发布环境。

## 安装模板

模板由 `scripts/seed_bugfix_workflow.py` 提供，固定 ID 为 `workflow_bugfix_demo`。

```bash
.venv/bin/python -m scripts.seed_bugfix_workflow
```

运行前请在执行环境中配置 `DATABASE_URL`。命令只更新固定 ID 的演示记录，不清空其他 Agent 或 Workflow。请勿把数据库连接串写入仓库文件、命令参数或命令输出。

## Agent 配置

| Agent ID | 名称 | 用途 |
| --- | --- | --- |
| `agent_defect_analysis` | 缺陷定位 Agent | 缺陷复现与影响评估 |
| `agent_code_review` | 代码评审 Agent | 模拟代码评审 |
| `agent_automation_testing` | 自动化测试 Agent | 模拟回归测试 |
| `agent_deployment_check` | 上线检查 Agent | 模拟上线前检查 |
| `agent_mock_release_execution` | Mock 发布执行 Agent | 生成模拟发布记录 |
| `agent_monitoring_alerting` | 监控告警 Agent | 模拟发布后观察 |

## 节点编排

```text
开始
-> 缺陷复现与影响评估
-> 人工模拟修复
-> 代码评审 + 回归测试（并行）
-> QA 人工门禁
-> 上线前检查
-> Mock 发布执行
-> 发布后观察
-> 完成
```

`代码评审` 和 `回归测试` 必须都成功，QA 人工门禁才会创建。

## Workflow API JSON

以下请求体与种子脚本中的 `BUGFIX_WORKFLOW_CREATE` 保持一致，可用于 `POST /api/v1/workflows`：

```json
{
  "name": "Bug 修复演示闭环",
  "description": "模拟完成缺陷分析、人工修复、代码评审、回归测试、QA 门禁、上线检查、发布和发布后观察。QA 仅在明确通过时进入发布阶段。",
  "definition": {
    "nodes": [
      {
        "id": "start",
        "type": "start",
        "title": "开始"
      },
      {
        "id": "defect_analysis",
        "type": "agent",
        "title": "缺陷复现与影响评估",
        "description": "模拟确认复现结果，并输出严重级别、影响模块、建议归属和风险。",
        "agent_id": "agent_defect_analysis"
      },
      {
        "id": "bug_fix_human",
        "type": "human",
        "title": "人工模拟修复",
        "description": "根据缺陷分析模拟完成修复并给出自测结果。",
        "config": {
          "assignee_user_id": "root",
          "assignee_user_name": "管理员",
          "assignee_role": "bug_fix_owner",
          "handoff_instruction": "请根据缺陷复现与影响评估结果模拟完成 Bug 修复，并说明根因、修改内容、影响范围和自测结果。"
        }
      },
      {
        "id": "code_review",
        "type": "agent",
        "title": "代码评审",
        "description": "模拟评审修复方案，输出质量问题、风险和上线阻塞项。",
        "agent_id": "agent_code_review"
      },
      {
        "id": "regression_test",
        "type": "agent",
        "title": "回归测试",
        "description": "模拟执行目标用例和回归用例，输出数量、失败项和结论。",
        "agent_id": "agent_automation_testing"
      },
      {
        "id": "qa_gate_human",
        "type": "human",
        "title": "QA 人工门禁",
        "description": "结合代码评审和回归测试结果决定是否允许发布。",
        "config": {
          "assignee_user_id": "root",
          "assignee_user_name": "管理员",
          "assignee_role": "qa_reviewer",
          "required_metadata": [
            "decision"
          ],
          "handoff_instruction": "请结合代码评审和回归测试结果进行 QA 审核。通过时提交 decision=approved；驳回时提交 decision=rejected；信息不足时提交 decision=need_more_info。"
        }
      },
      {
        "id": "deployment_check",
        "type": "agent",
        "title": "上线前检查",
        "description": "模拟检查版本、配置、依赖、灰度、回滚和监控准备。",
        "agent_id": "agent_deployment_check"
      },
      {
        "id": "mock_release",
        "type": "agent",
        "title": "Mock 发布执行",
        "description": "模拟生成发布版本、批次、时间和发布状态。",
        "agent_id": "agent_mock_release_execution"
      },
      {
        "id": "post_release_observation",
        "type": "agent",
        "title": "发布后观察",
        "description": "模拟观察核心指标、告警情况和发布结论。",
        "agent_id": "agent_monitoring_alerting"
      },
      {
        "id": "end",
        "type": "end",
        "title": "完成"
      }
    ],
    "edges": [
      {
        "from": "start",
        "to": "defect_analysis"
      },
      {
        "from": "defect_analysis",
        "to": "bug_fix_human"
      },
      {
        "from": "bug_fix_human",
        "to": "code_review"
      },
      {
        "from": "bug_fix_human",
        "to": "regression_test"
      },
      {
        "from": "code_review",
        "to": "qa_gate_human"
      },
      {
        "from": "regression_test",
        "to": "qa_gate_human"
      },
      {
        "from": "qa_gate_human",
        "to": "deployment_check",
        "condition": {
          "field": "decision",
          "operator": "eq",
          "value": "approved"
        }
      },
      {
        "from": "deployment_check",
        "to": "mock_release"
      },
      {
        "from": "mock_release",
        "to": "post_release_observation"
      },
      {
        "from": "post_release_observation",
        "to": "end"
      }
    ]
  }
}
```

`POST /api/v1/workflows` 会返回系统生成的 Workflow ID。若使用 API 创建而不是种子脚本安装，发起任务时应把 `metadata.workflow_id` 替换为响应中的 `id`；下文固定 ID 仅适用于种子安装路径。

## QA 决策

QA 通过时提交：

```json
{
  "result_status": "succeeded",
  "output": "QA 审核通过，可以发布。",
  "should_complete": false,
  "metadata": {"decision": "approved"}
}
```

QA 驳回或信息不足时仍以成功处理结果提交业务决策：

```json
{
  "result_status": "succeeded",
  "output": "QA 驳回：回归测试证据不足。",
  "should_complete": false,
  "metadata": {"decision": "rejected"}
}
```

只有 `decision=approved` 存在后继边。`rejected`、`need_more_info` 或缺少 `decision` 时，发布阶段不执行，主任务进入 `blocked`。

## 发起任务

```json
{
  "source_type": "business_system",
  "title": "客户登录状态失效 Bug 修复",
  "content": "模拟完成登录状态失效问题的分析、修复、测试和发布。",
  "metadata": {
    "execution_mode": "workflow_template",
    "workflow_id": "workflow_bugfix_demo"
  }
}
```

任务确认后先执行缺陷分析，并暂停在“人工模拟修复”。提交修复结果后，代码评审和回归测试并行执行，随后暂停在“QA 人工门禁”。

## 验证观察点

- 人工模拟修复和 QA 人工门禁会产生两次独立人工待办。
- 代码评审与回归测试位于同一并行轮次。
- QA 明确通过后才会出现上线前检查、Mock 发布和发布后观察。
- QA 驳回时任务状态为 `blocked`，完成报告没有 Workflow end 节点。
- 任一 Agent 执行失败时任务状态为 `failed`，后续节点不再执行。
- 模板不包含返工回环或智能条件判断节点。
````

- [ ] **Step 2: Check the documentation for obsolete flow instructions**

Run:

```bash
rg -n "qa_decision|bug_fix_human.*qa_decision|驳回返工|Mock Bug归属分析Agent|Mock Bug测试Agent" docs/test-cases/Bug修复流程场景说明.md
```

Expected: no output.

- [ ] **Step 3: Check Markdown whitespace**

Run:

```bash
git diff --check -- docs/test-cases/Bug修复流程场景说明.md
! rg -n "[[:blank:]]+$" docs/test-cases/Bug修复流程场景说明.md
```

Expected: both commands exit successfully with no output.

### Task 5: Final Verification

**Files:**

- Verify: `scripts/seed_bugfix_workflow.py`
- Verify: `tests/test_seed_bugfix_workflow.py`
- Verify: `tests/test_workflows.py`
- Verify: `docs/test-cases/Bug修复流程场景说明.md`

- [ ] **Step 1: Run focused Bugfix tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_seed_bugfix_workflow.py \
  tests/test_workflows.py::test_bugfix_demo_workflow_runs_two_human_gates_and_completes \
  tests/test_workflows.py::test_bugfix_demo_workflow_blocks_release_when_qa_rejects
```

Expected: `8 passed`.

- [ ] **Step 2: Run the complete Workflow test module**

Run:

```bash
.venv/bin/pytest -q tests/test_workflows.py
```

Expected: all tests pass.

- [ ] **Step 3: Run the complete backend test suite**

Run:

```bash
.venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run syntax and diff checks**

Run:

```bash
.venv/bin/python -m py_compile scripts/seed_bugfix_workflow.py
git diff --check -- \
  tests/test_workflows.py \
  docs/test-cases/Bug修复流程场景说明.md
! rg -n "[[:blank:]]+$" \
  scripts/seed_bugfix_workflow.py \
  tests/test_seed_bugfix_workflow.py \
  tests/test_workflows.py \
  docs/test-cases/Bug修复流程场景说明.md \
  docs/superpowers/specs/2026-07-23-bugfix-workflow-template-design.md \
  docs/superpowers/plans/2026-07-23-bugfix-workflow-template.md
```

Expected: all three commands exit successfully with no output or errors.

- [ ] **Step 5: Review the final diff without staging or committing**

Run:

```bash
git status --short
git diff -- \
  tests/test_workflows.py \
  docs/test-cases/Bug修复流程场景说明.md
sed -n '1,400p' scripts/seed_bugfix_workflow.py
sed -n '1,300p' tests/test_seed_bugfix_workflow.py
sed -n '1,280p' docs/superpowers/specs/2026-07-23-bugfix-workflow-template-design.md
sed -n '1,1600p' docs/superpowers/plans/2026-07-23-bugfix-workflow-template.md
```

Expected: tracked diffs and complete new files are reviewed; no Git commit is created.
