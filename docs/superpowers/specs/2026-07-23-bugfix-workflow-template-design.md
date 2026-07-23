# Bug 修复演示 Workflow 优化设计

## 背景

仓库原先仅在 `docs/test-cases/Bug修复流程场景说明.md` 中描述“Bug 修复上线 Workflow”。第一版优化已经提供数据库种子，但默认启动在未配置 `DATABASE_URL` 时使用 `app/data/agents.json` 和 `app/data/workflows.json`，两个文件仍为空，因此模板不会出现在默认流程模板列表。

现有最小模板存在两个关键问题：

- QA 人工节点到发布节点使用无条件连线，审核驳回仍可能继续发布。
- 文档建议的返工回环会重新指向已经成功的节点，但当前模板执行器不会再次执行相同节点，流程会进入阻塞状态。

本次选择优化为 Mock 演示闭环，不实现真实代码修复，也不扩展通用回环执行能力。

## 目标

- 提供一条逻辑完整、演示可信的 Bug 修复 Mock 流程。
- 覆盖缺陷分析、人工修复、代码评审、回归测试、QA 门禁、上线检查、发布和发布后观察。
- QA 只有明确通过后才能进入发布阶段。
- 复用现有生命周期 Agent，仅新增缺失的 Mock 发布执行 Agent。
- 提供独立、幂等、非破坏性的数据库与文件 registry 种子脚本。
- 将模板实际安装到默认文件 registry，使默认启动后可直接查看。
- 补充完整通过链路和 QA 驳回链路的自动化测试。

## 非目标

- 不让业务 Agent 真实修改代码、执行 Shell 测试或操作发布环境。
- 不实现节点重入、自动返工、循环次数控制或失败重试。
- 不修改通用 Workflow 执行器。
- 不修改前端编排画布。
- 不自动连接或修改当前开发数据库。
- 不在应用启动时自动写入 Agent 或 Workflow 数据文件。
- 不删除用户自建 Agent、Workflow 或历史模板。
- 不执行 Git 提交。

## 运行时约束

设计遵守当前 `WorkflowTemplateRunner` 的实际能力：

- 使用 `start`、`agent`、`human`、`end` 四种节点。
- 同时就绪的 `agent` 节点可以并行执行。
- 多条无条件入边表示所有前置节点均完成后才可执行。
- 条件边直接匹配前驱成功子任务的 `result_metadata`。
- 已成功节点不会再次执行，因此模板必须保持为无环 DAG。
- 任一 Agent 节点失败会使任务进入 `failed`。
- 人工节点提交非成功状态时，任务直接使用该终态。
- 条件分支无匹配路径且未到达 `end` 时，任务进入 `blocked`。

## 节点设计

模板固定 ID 为 `workflow_bugfix_demo`，名称为“Bug 修复演示闭环”。

| 顺序 | 节点 ID | 类型 | 标题 | 执行主体 | 输出要求 |
| --- | --- | --- | --- | --- | --- |
| 1 | `start` | start | 开始 | 系统 | 初始化任务上下文 |
| 2 | `defect_analysis` | agent | 缺陷复现与影响评估 | `agent_defect_analysis` | 复现结论、严重级别、影响模块、建议归属、风险 |
| 3 | `bug_fix_human` | human | 人工模拟修复 | `root` | 模拟根因、修改内容、影响范围、自测结果 |
| 4 | `code_review` | agent | 代码评审 | `agent_code_review` | 评审结论、质量问题、风险、上线阻塞项 |
| 5 | `regression_test` | agent | 回归测试 | `agent_automation_testing` | 测试范围、通过/失败数量、失败用例、测试结论 |
| 6 | `qa_gate_human` | human | QA 人工门禁 | `root` | 审核意见与 `metadata.decision` |
| 7 | `deployment_check` | agent | 上线前检查 | `agent_deployment_check` | 版本、配置、依赖、灰度、回滚、监控检查结果 |
| 8 | `mock_release` | agent | Mock 发布执行 | `agent_mock_release_execution` | 发布版本、批次、时间、状态 |
| 9 | `post_release_observation` | agent | 发布后观察 | `agent_monitoring_alerting` | 核心指标、告警情况、观察结论 |
| 10 | `end` | end | 完成 | 系统 | 汇总上下文并完成任务 |

## 连线设计

```text
start -> defect_analysis
defect_analysis -> bug_fix_human
bug_fix_human -> code_review
bug_fix_human -> regression_test
code_review -> qa_gate_human
regression_test -> qa_gate_human
qa_gate_human -> deployment_check  条件：decision == approved
deployment_check -> mock_release
mock_release -> post_release_observation
post_release_observation -> end
```

`code_review` 与 `regression_test` 在人工模拟修复完成后并行执行。`qa_gate_human` 有两条无条件入边，因此必须等待两个节点都成功。

QA 条件边使用：

```json
{
  "field": "decision",
  "operator": "eq",
  "value": "approved"
}
```

模板不增加智能条件节点，避免人工已经提交结构化决策后再次调用模型进行二次判断。

## 人工节点配置

### 人工模拟修复

```json
{
  "assignee_user_id": "root",
  "assignee_user_name": "管理员",
  "assignee_role": "bug_fix_owner",
  "handoff_instruction": "请根据缺陷复现与影响评估结果模拟完成 Bug 修复，并说明根因、修改内容、影响范围和自测结果。"
}
```

### QA 人工门禁

```json
{
  "assignee_user_id": "root",
  "assignee_user_name": "管理员",
  "assignee_role": "qa_reviewer",
  "required_metadata": ["decision"],
  "handoff_instruction": "请结合代码评审和回归测试结果进行 QA 审核。通过时提交 decision=approved；驳回时提交 decision=rejected；信息不足时提交 decision=need_more_info。"
}
```

业务上的驳回仍以 `result_status=succeeded` 提交，并通过 `metadata.decision` 表达审核结论。若人工直接提交 `failed`、`blocked` 或 `partial`，任务按现有服务语义立即结束。

## Agent 策略

复用以下生命周期 Agent：

- `agent_defect_analysis`
- `agent_code_review`
- `agent_automation_testing`
- `agent_deployment_check`
- `agent_monitoring_alerting`

新增 `agent_mock_release_execution`：

- 名称：`Mock 发布执行 Agent`
- 类型：`processing`
- 能力：`release_execution`、`mock_release`
- 输入：任务目标、累计上下文、上线检查结果
- 输出：发布版本、发布批次、发布时间、发布状态和观察建议
- 行为：只生成 Mock 发布结果，不调用真实发布接口

旧的 `Mock Bug归属分析Agent`、`Mock Bug测试Agent` 和 `Mock 上线发布Agent` 不再被新模板引用。种子脚本不主动删除这些历史记录，避免误删用户数据。

## 种子设计

`scripts/seed_bugfix_workflow.py` 同时提供数据库种子、文件 registry 种子和命令行入口。

种子行为：

- 使用固定 Agent ID 和 Workflow ID。
- 从生命周期种子中读取并确保五个依赖 Agent 存在。
- 依赖 Agent 已存在时保留现有记录，不覆盖用户调整。
- `agent_mock_release_execution` 不存在时创建，存在时更新为当前定义。
- `workflow_bugfix_demo` 不存在时创建，存在时更新为当前模板定义。
- 不执行整表删除。
- 不删除或覆盖其他 ID 的 Agent 和 Workflow。
- 同一数据库或同一组数据文件重复执行后，目标记录仍各只有一条。
- 数据库模式通过 `--database-url` 或 `DATABASE_URL` 接收数据库地址，不打印连接串。
- 文件模式必须同时提供 `--agent-file` 和 `--workflow-file`，按现有 registry JSON 格式读写。
- 显式 `--database-url` 与文件模式不能同时启用；完整文件参数优先于环境中的 `DATABASE_URL`，避免环境配置阻止一次性文件安装。
- 文件参数不完整或两种模式都未配置时明确报错。
- 文件模式保留已存在的生命周期 Agent 与所有无关 Agent/Workflow，仅更新 Mock 发布 Agent 和固定 Workflow。
- 文件写入使用 Pydantic 模型校验，并以 UTF-8、格式化 JSON 保存。

本次不会连接当前开发数据库。文件模式测试通过后，对 `app/data/agents.json` 和 `app/data/workflows.json` 执行一次幂等安装；应用启动本身不自动执行种子。

## 数据流

每个成功节点的文本输出进入任务累计上下文：

1. 缺陷分析结果交给人工模拟修复。
2. 人工修复结果同时交给代码评审和回归测试。
3. 两个并行结果共同交给 QA 人工门禁。
4. QA 通过后，累计上下文依次流入上线检查、Mock 发布和发布后观察。
5. `end` 使用累计上下文生成最终交付结果。

节点描述和 Agent system prompt 负责约束输出内容。当前运行时不会强校验 Agent `output_schema`，因此测试验证流程和路由，不把自由文本字段解析作为完成条件。

## 失败与阻塞语义

- `qa_gate_human` 提交 `decision=approved`：进入上线检查并继续执行。
- 提交 `decision=rejected`：没有匹配的后继边，发布节点被跳过，任务进入 `blocked`。
- 提交 `decision=need_more_info`：没有匹配的后继边，任务进入 `blocked`。
- 缺少 `decision`：条件边不匹配，任务进入 `blocked`。
- 任一 Agent 执行失败：任务进入 `failed`，后续节点不执行。
- 人工节点提交 `failed`、`blocked` 或 `partial`：任务立即进入相应终态。
- 不配置自动返工、发布失败补偿或回滚分支。

## 文件范围

新增：

- `scripts/seed_bugfix_workflow.py`
- `tests/test_seed_bugfix_workflow.py`

修改：

- `app/data/agents.json`
- `app/data/workflows.json`
- `docs/test-cases/Bug修复流程场景说明.md`
- `tests/test_workflows.py`

不修改：

- `app/workflows/template_runner.py`
- `app/workflows/task_graph.py`
- `frontend/`

## 测试设计

### 种子测试

- 固定模板包含 10 个节点和 10 条连线。
- 模板引用的六个 Processing Agent 均存在。
- QA 到上线检查的边只接受 `decision=approved`。
- 首次执行种子能够创建缺失记录。
- 第二次执行种子不会生成重复记录。
- 预先存在的无关 Agent 和 Workflow 保持不变。
- 已存在的生命周期 Agent 不被覆盖。
- Mock 发布 Agent 和目标 Workflow 可以更新到最新定义。
- 文件 registry 首次安装能够创建六个目标 Agent 和固定 Workflow。
- 文件 registry 重复安装不会产生重复记录，并保留无关记录和用户自定义生命周期 Agent。
- CLI 会拒绝数据库与文件模式同时配置、单个文件参数和完全缺少存储参数。

### Workflow 通过链路

- 确认任务后先执行缺陷分析，再暂停在人工模拟修复。
- 提交人工修复结果后，并行完成代码评审和回归测试。
- 两个并行节点全部成功后，暂停在 QA 人工门禁。
- QA 提交 `decision=approved` 后依次执行上线检查、Mock 发布、发布后观察。
- 最终任务到达 `end` 并进入 `succeeded`。

### Workflow 驳回链路

- 流程执行到 QA 人工门禁。
- QA 以成功结果提交 `decision=rejected`。
- 任务进入 `blocked`。
- 上线检查、Mock 发布和发布后观察均未执行。
- 完成报告不得记录已到达 Workflow end。

## 验收标准

- 模板节点顺序、并行关系和 QA 门禁与本设计一致。
- QA 非明确通过时不会执行任何发布阶段节点。
- 种子脚本可重复执行且不破坏其他数据。
- 默认 `app/data` 文件包含固定 Workflow 和六个被引用的 Agent，默认文件模式可以直接列出该模板。
- 新模板不包含回环或智能条件节点。
- 场景文档与实际可执行 JSON 一致。
- 新增和相关现有测试全部通过。
- 实现过程不执行 Git 提交，也不连接当前开发数据库运行种子。
- 应用启动过程不会隐式新增或更新模板数据。
