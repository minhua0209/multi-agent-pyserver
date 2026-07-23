# Bug 修复流程场景说明

## 场景目标

验证“缺陷分析、人工模拟修复、并行代码评审与回归测试、QA 人工门禁、上线检查、Mock 发布、发布后观察”的完整演示闭环。所有修复、测试和发布结果均为 Mock，不修改真实代码或发布环境。

## 安装模板

模板由 `scripts/seed_bugfix_workflow.py` 提供，固定 ID 为 `workflow_bugfix_demo`。

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

数据库模式运行前需在执行环境配置 `DATABASE_URL`。两种模式都只更新固定 ID 的演示记录，不清空其他 Agent 或 Workflow。请勿把数据库连接串写入仓库文件、命令参数或命令输出。

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
