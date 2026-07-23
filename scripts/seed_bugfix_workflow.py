from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

from app.core.config import DEFAULT_DATABASE_URL
from app.core.models import (
    Agent,
    AgentCreate,
    WorkflowCreate,
    WorkflowTemplate,
    utc_now,
)
from app.services.storage import (
    _json_dump,
    agents_table,
    workflow_templates_table,
)
from scripts.seed_lifecycle_agents import LIFECYCLE_AGENT_SEEDS, _ensure_agents_schema


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
        "tools_json": _json_dump(
            [tool.model_dump(mode="json") for tool in agent.tools]
        ),
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


def seed_bugfix_workflow(
    database_url: str,
) -> tuple[list[Agent], WorkflowTemplate]:
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

            created_at = (
                existing["created_at"]
                if existing is not None and existing["created_at"]
                else now
            )
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
            status=(
                str(existing_workflow["status"])
                if existing_workflow is not None
                else "active"
            ),
            created_at=(
                existing_workflow["created_at"]
                if existing_workflow is not None and existing_workflow["created_at"]
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


def _read_json_array(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []
    raw_items = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list):
        raise ValueError(f"Expected a JSON array in {file_path}")
    return raw_items


def _write_json_models(
    file_path: Path,
    items: list[Agent | WorkflowTemplate],
) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the Bugfix demo Workflow and required Agents."
    )
    parser.add_argument("--database-url")
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


if __name__ == "__main__":
    main()
