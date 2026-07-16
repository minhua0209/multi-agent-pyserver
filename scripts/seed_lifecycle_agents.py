from __future__ import annotations

import argparse
import os
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, delete

from app.core.config import DEFAULT_DATABASE_URL
from app.core.models import Agent, AgentCreate, utc_now
from app.services.storage import _ensure_column, _json_dump, agents_table, metadata


def _seed(
    agent_id: str,
    name: str,
    stage: str,
    description: str,
    capabilities: list[str],
    output_context: list[str],
    icon: str,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": name,
        "description": description,
        "agent_type": "processing",
        "capabilities": capabilities,
        "input_schema": {
            "context_inputs": ["task.content", "context.summary", "subtask.output"],
            "required": ["任务目标", "当前上下文"],
        },
        "output_schema": {
            "context_outputs": output_context,
            "required": ["结论", "风险", "下一步建议"],
        },
        "execution_config": {
            "system_prompt": (
                f"你是{name}，负责软件交付流程中的{stage}阶段。"
                "请基于任务上下文输出结构化结论、风险点和下一步建议。"
            ),
            "timeout_seconds": 90,
            "max_retries": 1,
            "max_tool_calls": 0,
        },
        "tools": [],
        "metadata": {
            "stage": stage,
            "icon": icon,
            "seed_version": "2026-07-16-lifecycle-agents",
        },
    }


LIFECYCLE_AGENT_SEEDS: list[dict[str, Any]] = [
    _seed(
        "agent_requirement_analysis",
        "需求分析 Agent",
        "需求",
        "梳理业务诉求、目标用户、业务价值和范围边界，识别需求缺口。",
        ["requirement_analysis", "scope_definition"],
        ["requirement.summary", "requirement.risks"],
        "FileSearch",
    ),
    _seed(
        "agent_prd_refinement",
        "PRD 完善 Agent",
        "需求",
        "把需求整理为 PRD 结构，补充功能说明、约束、异常流程和验收口径。",
        ["prd_refinement", "acceptance_criteria"],
        ["prd.sections", "acceptance.criteria"],
        "ClipboardList",
    ),
    _seed(
        "agent_user_story",
        "用户故事拆解 Agent",
        "需求",
        "按角色、场景和目标拆分用户故事，并输出可排期的任务条目。",
        ["user_story", "task_breakdown"],
        ["story.list", "task.breakdown"],
        "ListChecks",
    ),
    _seed(
        "agent_solution_design",
        "技术方案 Agent",
        "设计",
        "根据需求输出技术方案、模块边界、依赖关系和关键风险。",
        ["solution_design", "architecture_review"],
        ["solution.summary", "architecture.risks"],
        "Boxes",
    ),
    _seed(
        "agent_api_design",
        "接口设计 Agent",
        "设计",
        "设计接口契约、请求响应字段、错误码、幂等性和兼容性策略。",
        ["api_design", "contract_design"],
        ["api.contracts", "api.risks"],
        "Network",
    ),
    _seed(
        "agent_data_model_design",
        "数据模型 Agent",
        "设计",
        "设计数据表、字段、索引、状态流转和数据迁移注意事项。",
        ["data_model_design", "migration_planning"],
        ["data.model", "migration.notes"],
        "Database",
    ),
    _seed(
        "agent_frontend_development",
        "前端研发 Agent",
        "研发",
        "负责前端页面、交互状态、接口联调和可用性问题分析。",
        ["frontend_development", "ui_integration"],
        ["frontend.plan", "frontend.risks"],
        "Monitor",
    ),
    _seed(
        "agent_backend_development",
        "后端研发 Agent",
        "研发",
        "负责接口实现、服务编排、数据持久化和异常处理方案。",
        ["backend_development", "service_orchestration"],
        ["backend.plan", "backend.risks"],
        "ServerCog",
    ),
    _seed(
        "agent_integration_development",
        "联调集成 Agent",
        "研发",
        "梳理前后端、外部系统和任务流的联调步骤、依赖和阻塞点。",
        ["integration_development", "dependency_check"],
        ["integration.plan", "dependency.risks"],
        "Cable",
    ),
    _seed(
        "agent_code_review",
        "代码评审 Agent",
        "研发",
        "检查实现质量、边界条件、可维护性、安全风险和缺失测试。",
        ["code_review", "quality_gate"],
        ["review.findings", "quality.risks"],
        "GitPullRequest",
    ),
    _seed(
        "agent_test_case_design",
        "测试用例 Agent",
        "测试",
        "根据需求和方案生成测试场景、用例、前置条件和验收数据。",
        ["test_case_design", "acceptance_testing"],
        ["test.cases", "test.data"],
        "FileCheck",
    ),
    _seed(
        "agent_automation_testing",
        "自动化测试 Agent",
        "测试",
        "规划接口、前端或回归自动化测试范围，输出测试脚本建议。",
        ["automation_testing", "regression_testing"],
        ["automation.plan", "regression.scope"],
        "Bot",
    ),
    _seed(
        "agent_defect_analysis",
        "缺陷定位 Agent",
        "测试",
        "分析缺陷复现步骤、影响范围、可能根因和修复优先级。",
        ["defect_analysis", "root_cause_analysis"],
        ["defect.analysis", "fix.priority"],
        "Bug",
    ),
    _seed(
        "agent_release_planning",
        "发布计划 Agent",
        "上线",
        "制定发布窗口、依赖确认、变更清单、通知计划和负责人安排。",
        ["release_planning", "change_management"],
        ["release.plan", "change.list"],
        "CalendarClock",
    ),
    _seed(
        "agent_deployment_check",
        "上线检查 Agent",
        "上线",
        "检查配置、版本、数据库变更、灰度策略、监控和验收项。",
        ["deployment_check", "go_live_checklist"],
        ["deployment.checklist", "go_live.risks"],
        "Rocket",
    ),
    _seed(
        "agent_rollback_planning",
        "回滚预案 Agent",
        "上线",
        "制定回滚触发条件、回滚步骤、数据恢复和沟通方案。",
        ["rollback_planning", "contingency_plan"],
        ["rollback.plan", "contingency.risks"],
        "Undo2",
    ),
    _seed(
        "agent_monitoring_alerting",
        "监控告警 Agent",
        "运维",
        "设计核心指标、告警阈值、通知策略和故障升级路径。",
        ["monitoring_alerting", "slo_tracking"],
        ["monitoring.plan", "alert.rules"],
        "Activity",
    ),
    _seed(
        "agent_log_diagnosis",
        "日志诊断 Agent",
        "运维",
        "分析日志、错误堆栈、请求链路和异常模式，输出排查方向。",
        ["log_diagnosis", "incident_triage"],
        ["diagnosis.summary", "incident.clues"],
        "SearchCode",
    ),
    _seed(
        "agent_performance_tuning",
        "性能容量 Agent",
        "运维",
        "评估性能瓶颈、容量水位、扩容策略和优化优先级。",
        ["performance_tuning", "capacity_planning"],
        ["performance.findings", "capacity.plan"],
        "Gauge",
    ),
    _seed(
        "agent_incident_review",
        "故障复盘 Agent",
        "运维",
        "整理事故时间线、影响面、根因、改进项和跟进责任人。",
        ["incident_review", "postmortem"],
        ["incident.review", "action.items"],
        "History",
    ),
]


def _ensure_agents_schema(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    metadata.create_all(engine)
    _ensure_column(engine, "agents", "payload", "TEXT NULL")
    _ensure_column(engine, "agents", "name", "VARCHAR(255) NULL")
    _ensure_column(engine, "agents", "description", "TEXT NULL")
    _ensure_column(engine, "agents", "agent_type", "VARCHAR(64) NOT NULL DEFAULT 'processing'")
    _ensure_column(engine, "agents", "capabilities_json", "TEXT NULL")
    _ensure_column(engine, "agents", "input_schema_json", "TEXT NULL")
    _ensure_column(engine, "agents", "output_schema_json", "TEXT NULL")
    _ensure_column(engine, "agents", "execution_config_json", "TEXT NULL")
    _ensure_column(engine, "agents", "tools_json", "TEXT NULL")
    _ensure_column(engine, "agents", "metadata_json", "TEXT NULL")
    _ensure_column(engine, "agents", "status", "VARCHAR(32) NOT NULL DEFAULT 'active'")
    _ensure_column(engine, "agents", "created_at", "DATETIME NULL")
    _ensure_column(engine, "agents", "updated_at", "DATETIME NULL")


def _agent_from_seed(seed: dict[str, Any], created_at: datetime) -> Agent:
    payload = AgentCreate.model_validate(seed)
    return Agent(id=str(seed["id"]), created_at=created_at, **payload.model_dump())


def seed_lifecycle_agents(database_url: str) -> list[Agent]:
    _ensure_agents_schema(database_url)
    now = utc_now()
    agents = [_agent_from_seed(seed, now) for seed in LIFECYCLE_AGENT_SEEDS]
    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        connection.execute(delete(agents_table))
        connection.execute(
            agents_table.insert(),
            [
                {
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
                    "updated_at": agent.created_at,
                }
                for agent in agents
            ],
        )
    return agents


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed lifecycle Agent nodes into the configured database.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
    args = parser.parse_args()
    agents = seed_lifecycle_agents(args.database_url)
    print(f"Seeded {len(agents)} lifecycle agents.")


if __name__ == "__main__":
    main()
