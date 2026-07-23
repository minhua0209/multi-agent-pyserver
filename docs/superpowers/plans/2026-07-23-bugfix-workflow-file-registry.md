# Bugfix Workflow File Registry Installation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 Bug 修复 Workflow 种子以支持文件 registry，并将固定模板和依赖 Agent 幂等安装到默认 `app/data`，使默认启动后可直接查看。

**Architecture:** 保留现有数据库种子行为，新增独立的 `seed_bugfix_workflow_files()`，按固定 ID 对 Agent 与 Workflow JSON 数组执行非破坏性 upsert。CLI 使用互斥的数据源参数选择数据库或文件模式，不修改应用启动逻辑；测试通过后显式运行文件种子安装默认数据。

**Tech Stack:** Python 3.12、Pydantic、JSON、pytest、FastAPI file registry。

**Repository Constraint:** 按仓库 `AGENTS.md`，本计划不包含 Git 暂存或提交步骤。

---

## File Map

- Modify `scripts/seed_bugfix_workflow.py`: 增加文件 registry upsert 与 CLI 双模式选择。
- Modify `tests/test_seed_bugfix_workflow.py`: 增加文件幂等、数据保留和 CLI 参数测试。
- Modify `docs/test-cases/Bug修复流程场景说明.md`: 增加默认文件模式安装命令。
- Modify `app/data/agents.json`: 通过种子命令安装六个目标 Agent。
- Modify `app/data/workflows.json`: 通过种子命令安装固定 Bug 修复 Workflow。

### Task 1: Add File Registry Seeding

**Files:**

- Modify: `tests/test_seed_bugfix_workflow.py`
- Modify: `scripts/seed_bugfix_workflow.py`

- [ ] **Step 1: Write the failing file registry tests**

Append to `tests/test_seed_bugfix_workflow.py`:

```python
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
```

Also add `WorkflowTemplate` to the existing `app.core.models` import.

- [ ] **Step 2: Run the file registry tests and verify RED**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_seed_bugfix_workflow.py::test_seed_bugfix_workflow_files_creates_missing_records_and_is_idempotent \
  tests/test_seed_bugfix_workflow.py::test_seed_bugfix_workflow_files_preserves_unrelated_and_custom_records
```

Expected: both tests FAIL with `AttributeError` because `seed_bugfix_workflow_files` does not exist.

- [ ] **Step 3: Implement file registry upsert**

Add `json` and `Path` to the standard-library imports in `scripts/seed_bugfix_workflow.py`:

```python
import json
from pathlib import Path
```

Append before `main()`:

```python
def _read_json_array(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []
    raw_items = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list):
        raise ValueError(f"Expected a JSON array in {file_path}")
    return raw_items


def _write_json_models(file_path: Path, items: list[Agent | WorkflowTemplate]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(
            [item.model_dump(mode="json", by_alias=True) for item in items],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def seed_bugfix_workflow_files(
    agent_file: Path,
    workflow_file: Path,
) -> tuple[list[Agent], WorkflowTemplate]:
    existing_agents = [
        Agent.model_validate(item)
        for item in _read_json_array(agent_file)
    ]
    existing_workflows = [
        WorkflowTemplate.model_validate(item)
        for item in _read_json_array(workflow_file)
    ]
    agents_by_id = {agent.id: agent for agent in existing_agents}
    workflows_by_id = {workflow.id: workflow for workflow in existing_workflows}
    target_agent_ids = {str(seed["id"]) for seed in BUGFIX_AGENT_SEEDS}
    now = utc_now()
    seeded_agents: list[Agent] = []

    for seed in BUGFIX_AGENT_SEEDS:
        agent_id = str(seed["id"])
        existing = agents_by_id.get(agent_id)
        if existing is not None and agent_id != MOCK_RELEASE_AGENT_ID:
            seeded_agents.append(existing)
            continue
        created_at = existing.created_at if existing is not None else now
        seeded_agents.append(_agent_from_seed(seed, created_at))

    existing_workflow = workflows_by_id.get(BUGFIX_WORKFLOW_ID)
    workflow = WorkflowTemplate(
        id=BUGFIX_WORKFLOW_ID,
        status=existing_workflow.status if existing_workflow is not None else "active",
        created_at=existing_workflow.created_at if existing_workflow is not None else now,
        updated_at=now,
        **BUGFIX_WORKFLOW_CREATE.model_dump(by_alias=True),
    )
    all_agents = [
        agent for agent in existing_agents if agent.id not in target_agent_ids
    ] + seeded_agents
    all_workflows = [
        existing
        for existing in existing_workflows
        if existing.id != BUGFIX_WORKFLOW_ID
    ] + [workflow]
    _write_json_models(agent_file, all_agents)
    _write_json_models(workflow_file, all_workflows)
    return seeded_agents, workflow
```

- [ ] **Step 4: Run the file registry tests and verify GREEN**

Run the two tests from Step 2 again.

Expected: `2 passed`.

### Task 2: Add Explicit CLI File Mode

**Files:**

- Modify: `tests/test_seed_bugfix_workflow.py`
- Modify: `scripts/seed_bugfix_workflow.py`

- [ ] **Step 1: Write failing CLI mode tests**

Keep `test_seed_cli_requires_url_and_does_not_echo_configured_url`, updating its missing-storage assertion to:

```python
assert (
    "--database-url/DATABASE_URL or both --agent-file and --workflow-file are required"
    in missing_url_output.err
)
```

Append:

```python
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
```

- [ ] **Step 2: Run the CLI tests and verify RED**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_seed_bugfix_workflow.py::test_seed_cli_rejects_incomplete_or_explicitly_combined_file_mode \
  tests/test_seed_bugfix_workflow.py::test_seed_cli_installs_file_registry_even_when_database_env_exists
```

Expected: CLI rejects `--agent-file` and `--workflow-file` as unknown arguments.

- [ ] **Step 3: Implement mutually exclusive storage selection**

Replace `main()` with:

```python
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the Bugfix demo Workflow and required Agents."
    )
    parser.add_argument(
        "--database-url",
    )
    parser.add_argument("--agent-file", type=Path)
    parser.add_argument("--workflow-file", type=Path)
    args = parser.parse_args()
    file_mode_requested = args.agent_file is not None or args.workflow_file is not None
    configured_database_url = (
        args.database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    )

    if args.database_url is not None and file_mode_requested:
        parser.error("database and file storage modes cannot be combined")
    if file_mode_requested and (args.agent_file is None or args.workflow_file is None):
        parser.error("--agent-file and --workflow-file must be provided together")
    if not configured_database_url and not file_mode_requested:
        parser.error(
            "--database-url/DATABASE_URL or both --agent-file and --workflow-file are required"
        )

    if file_mode_requested:
        agents, workflow = seed_bugfix_workflow_files(
            args.agent_file,
            args.workflow_file,
        )
        storage_mode = "file"
    else:
        agents, workflow = seed_bugfix_workflow(configured_database_url)
        storage_mode = "database"
    print(
        f"Seeded {len(agents)} agents and workflow {workflow.id} "
        f"in {storage_mode} storage."
    )
```

- [ ] **Step 4: Run all seed tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py
```

Expected: `10 passed`.

### Task 3: Install The Default File Registry

**Files:**

- Modify: `docs/test-cases/Bug修复流程场景说明.md`
- Modify: `app/data/agents.json`
- Modify: `app/data/workflows.json`

- [ ] **Step 1: Update the installation documentation**

Replace the single installation command with:

````markdown
默认文件模式：

```bash
.venv/bin/python -m scripts.seed_bugfix_workflow \
  --agent-file app/data/agents.json \
  --workflow-file app/data/workflows.json
```

数据库模式：

```bash
.venv/bin/python -m scripts.seed_bugfix_workflow
```

数据库模式运行前需在执行环境配置 `DATABASE_URL`。两种模式都只更新固定 ID 的演示记录，不清空其他 Agent 或 Workflow。
````

- [ ] **Step 2: Install the template into the default files**

Run:

```bash
.venv/bin/python -m scripts.seed_bugfix_workflow \
  --agent-file app/data/agents.json \
  --workflow-file app/data/workflows.json
```

Expected: `Seeded 6 agents and workflow workflow_bugfix_demo in file storage.`

- [ ] **Step 3: Verify the file-backed application can list the template**

Run:

```bash
.venv/bin/python -c 'import os; os.environ.pop("DATABASE_URL", None); os.environ["DISABLE_DEFAULT_DATABASE_URL"]="true"; from app.main import create_app; app=create_app(); workflows=app.state.workflow_registry.list_workflows(); agents=app.state.agent_registry.list_agents(); assert [item.id for item in workflows if item.id == "workflow_bugfix_demo"] == ["workflow_bugfix_demo"]; assert len([item for item in agents if item.id in {"agent_defect_analysis", "agent_code_review", "agent_automation_testing", "agent_deployment_check", "agent_monitoring_alerting", "agent_mock_release_execution"}]) == 6; print("default-file-registry-ok")'
```

Expected: `default-file-registry-ok`.

### Task 4: Final Verification

**Files:**

- Verify: `scripts/seed_bugfix_workflow.py`
- Verify: `tests/test_seed_bugfix_workflow.py`
- Verify: `tests/test_workflows.py`
- Verify: `app/data/agents.json`
- Verify: `app/data/workflows.json`
- Verify: `docs/test-cases/Bug修复流程场景说明.md`

- [ ] **Step 1: Run seed and Workflow tests**

Run:

```bash
.venv/bin/pytest -q tests/test_seed_bugfix_workflow.py tests/test_workflows.py
```

Expected: `33 passed`.

- [ ] **Step 2: Run the complete backend suite**

Run:

```bash
.venv/bin/pytest -q
```

Expected: no new failures beyond the pre-existing `tests/test_main.py::test_main_can_be_run_as_script` host assertion.

- [ ] **Step 3: Run syntax and file checks**

Run:

```bash
.venv/bin/python -m py_compile scripts/seed_bugfix_workflow.py
git diff --check -- \
  app/data/agents.json \
  app/data/workflows.json \
  docs/test-cases/Bug修复流程场景说明.md \
  tests/test_workflows.py
! rg -n "[[:blank:]]+$" \
  scripts/seed_bugfix_workflow.py \
  tests/test_seed_bugfix_workflow.py \
  docs/test-cases/Bug修复流程场景说明.md
```

Expected: all commands exit successfully with no output or errors.

- [ ] **Step 4: Review status without staging or committing**

Run:

```bash
git status --short
```

Expected: intended Bugfix files are modified or untracked alongside pre-existing unrelated user changes; no Git commit is created.
