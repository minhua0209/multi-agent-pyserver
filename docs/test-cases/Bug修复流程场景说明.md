# Bug 修复流程场景说明

## 场景目标

验证一个“测试人员提交 Bug，系统协同 Agent 与人工节点完成修复、测试、二次审核和上线”的闭环。

这个场景里，真正改 Bug、测试和上线都用 mock 结果代替；重点验证任务协同中心是否能正确完成：

- 动态拆分任务或按流程模板执行。
- Agent 节点自动处理。
- 人工节点挂起并交给指定人员。
- 本地 runner 可以领取人工节点任务并回填结果。
- 后续节点基于前置结果继续流转。

## 前置节点

建议先在“流程节点管理”里准备以下节点。节点 ID 以当前环境为准，配置流程模板时从页面选择节点即可。

| 节点名称 | 节点类型 | 用途 | 关键配置 |
| --- | --- | --- | --- |
| Mock Bug归属分析Agent | Agent 节点 | 判断 Bug 应该归属给谁处理 | `agent_type=processing`，能力可写 `bug_triage`、`owner_routing`、`缺陷归属` |
| Bug修复人工处理节点 | 人工节点 | 让管理员或本地 runner 托管处理 Bug | `agent_type=human`，审批人/处理人填 `管理员` 或 `root` |
| Mock Bug测试Agent | Agent 节点 | 模拟执行回归测试 | `agent_type=processing`，能力可写 `bug_test`、`qa_validation`、`测试验证` |
| 测试二次审核人工节点 | 人工节点 | 测试人员或管理员二次确认测试结果 | `agent_type=human`，审批人/处理人填 `管理员` 或指定测试人员 |
| Mock 上线发布Agent | Agent 节点 | 模拟灰度上线和发布观察 | `agent_type=processing`，能力可写 `release_deploy`、`mock_release`、`上线发布` |

mock 工具建议返回中文结果，方便详情页展示。例如：

```text
Bug归属分析完成：该问题属于前端登录态与会话刷新逻辑，建议归属给登录模块修复负责人。
```

```text
Mock测试完成：已验证登录成功、登录态过期、刷新页面重试等用例，结果全部通过。
```

```text
Mock上线完成：已完成灰度发布并观察15分钟，登录链路指标正常，发布完成。
```

## 无流程模板模式

无流程模板时，系统会先走意图识别，再由分发 Agent 根据当前上下文决定下一轮子任务。任务诉求要写得足够明确，尤其要明确哪些步骤必须人工介入。

### 推荐任务名称

```text
客户B登录问题修复
```

### 推荐任务诉求

```text
客户B反馈登录有问题：用户输入正确账号和密码后，有时候会提示“登录状态已失效”，刷新页面后又能正常登录。

帮我处理一下这个问题。

先判断这个问题应该归谁处理。确认归属后，安排管理员处理这个 Bug，修复过程不用真的改代码，可以用模拟结果表示已经修好了。

修好以后，再安排测试验证一下登录流程是否正常，测试也可以用模拟结果。

测试通过后，还需要管理员再确认一次测试结果。管理员确认没问题以后，再安排上线发布，上线也用模拟结果就行。

注意：
- Bug 修复这一步必须进入人工处理节点，让本地 runner 可以领取并处理。
- 测试结果二次审核也必须进入人工节点，让管理员确认。
- 管理员确认测试通过后，才能继续上线。
```

### 期望流转

```text
意图识别
-> 人工确认任务清单
-> 第 1 轮：Mock Bug归属分析Agent
-> 第 2 轮：Bug修复人工处理节点
-> 第 3 轮：Mock Bug测试Agent
-> 第 4 轮：测试二次审核人工节点
-> 第 5 轮：Mock 上线发布Agent
-> 完成
```

### 写法注意点

- 不要只写“帮我处理这个 Bug”，否则分发 Agent 可能直接生成普通处理任务，不一定会产生人工节点。
- 要明确写“Bug 修复这一步必须进入人工处理节点”。
- 要明确写“测试结果二次审核也必须进入人工节点”。
- 如果希望 runner 自动处理人工节点，启动 runner 时允许自动提交。
- 如果希望在 runner Web 控制台里看到待处理人工节点，启动 runner 时关闭自动提交。

runner 手动观察模式示例：

```bash
TASKHUB_AUTO_SUBMIT=false ./taskhub-codex-runner/start_runner.sh http://192.168.170.18:8000 root --ui
```

runner 自动托管模式示例：

```bash
./taskhub-codex-runner/start_runner.sh http://192.168.170.18:8000 root --ui
```

当前 runner 的自动提交是全局策略。也就是说，如果开启自动托管，Bug 修复人工节点和测试二次审核人工节点都可能被 runner 自动处理；如果需要“修复节点自动、审核节点必须人工点确认”，后续需要再加按节点角色控制的 runner 策略。

## 有流程模板模式

有流程模板时，执行顺序由模板固定，任务诉求可以写得更自然。这里不依赖分发 Agent 去猜下一步，因此更适合稳定演示。

### 模板名称

```text
Bug 修复上线 Workflow
```

### 最小闭环节点配置

| 顺序 | 节点 ID 建议 | 节点类型 | 节点标题 | 配置说明 |
| --- | --- | --- | --- | --- |
| 1 | `start` | start | 开始 | 系统默认开始节点 |
| 2 | `bug_triage` | agent | 判断 Bug 归属 | 绑定 `Mock Bug归属分析Agent` |
| 3 | `bug_fix_human` | human | 人工处理 Bug | 处理人填 `root / 管理员`，交代说明写“请根据归属分析处理 Bug，修复结果可用 mock 描述” |
| 4 | `bug_test` | agent | 回归测试 | 绑定 `Mock Bug测试Agent` |
| 5 | `qa_review_human` | human | 测试二次审核 | 处理人填 `root / 管理员` 或测试人员，交代说明写“请确认测试结果是否通过” |
| 6 | `release` | agent | 上线发布 | 绑定 `Mock 上线发布Agent` |
| 7 | `end` | end | 完成 | 系统默认完成节点 |

### 连线配置

```text
start -> bug_triage
bug_triage -> bug_fix_human
bug_fix_human -> bug_test
bug_test -> qa_review_human
qa_review_human -> release
release -> end
```

这个最小模板不配置分支，适合先验证主流程跑通。

### 可选：审核驳回返工分支

如果要验证“测试二次审核驳回后回到修复节点”，可以增加一个条件节点：

| 节点 ID 建议 | 节点类型 | 节点标题 | 配置 |
| --- | --- | --- | --- |
| `qa_decision` | condition | 判断测试审核结果 | `mode=rule`，`source_node_id=qa_review_human`，`field=decision`，`allowed_decisions=["approved","rejected"]`，`default_decision=rejected` |

连线改为：

```text
bug_test -> qa_review_human
qa_review_human -> qa_decision
qa_decision -> release        条件：{"type":"decision","value":"approved"}
qa_decision -> bug_fix_human  条件：{"type":"decision","value":"rejected"}
release -> end
```

人工提交测试二次审核结果时，metadata 需要带：

```json
{
  "decision": "approved"
}
```

或：

```json
{
  "decision": "rejected"
}
```

### Workflow 接口示例

如果不用页面，也可以通过接口创建模板。下面示例里的 `agent_id` 需要替换为当前环境中对应 Agent 节点的真实 ID。

```json
{
  "name": "Bug 修复上线 Workflow",
  "description": "先判断 Bug 归属，再由人工处理 Bug，随后测试、人工二次审核，最后模拟上线。",
  "definition": {
    "nodes": [
      {"id": "start", "type": "start", "title": "开始"},
      {
        "id": "bug_triage",
        "type": "agent",
        "title": "判断 Bug 归属",
        "description": "根据 Bug 现象判断归属团队和建议处理人。",
        "agent_id": "<Mock Bug归属分析Agent 的 ID>"
      },
      {
        "id": "bug_fix_human",
        "type": "human",
        "title": "人工处理 Bug",
        "description": "根据归属分析处理 Bug，修复结果可用 mock 描述。",
        "config": {
          "assignee_user_id": "root",
          "assignee_user_name": "管理员",
          "assignee_role": "bug_fix_owner",
          "handoff_instruction": "请处理登录状态失效问题，修复过程可用模拟结果描述。"
        }
      },
      {
        "id": "bug_test",
        "type": "agent",
        "title": "回归测试",
        "description": "模拟验证登录流程和相关回归用例。",
        "agent_id": "<Mock Bug测试Agent 的 ID>"
      },
      {
        "id": "qa_review_human",
        "type": "human",
        "title": "测试二次审核",
        "description": "确认测试结果是否通过。",
        "config": {
          "assignee_user_id": "root",
          "assignee_user_name": "管理员",
          "assignee_role": "qa_reviewer",
          "handoff_instruction": "请审核测试结果，通过后继续上线。"
        }
      },
      {
        "id": "release",
        "type": "agent",
        "title": "上线发布",
        "description": "模拟执行灰度上线和发布观察。",
        "agent_id": "<Mock 上线发布Agent 的 ID>"
      },
      {"id": "end", "type": "end", "title": "完成"}
    ],
    "edges": [
      {"from": "start", "to": "bug_triage"},
      {"from": "bug_triage", "to": "bug_fix_human"},
      {"from": "bug_fix_human", "to": "bug_test"},
      {"from": "bug_test", "to": "qa_review_human"},
      {"from": "qa_review_human", "to": "release"},
      {"from": "release", "to": "end"}
    ]
  }
}
```

### 有模板任务名称

```text
客户B登录问题修复
```

### 有模板任务诉求

```text
客户B反馈登录有问题：用户输入正确账号和密码后，有时候会提示“登录状态已失效”，刷新页面后又能正常登录。

请按我选择的 Bug 修复上线流程模板处理。

先判断问题归属，再由管理员处理 Bug。修复过程不用真的改代码，用模拟结果说明已经修好即可。修好后执行模拟回归测试，测试结果需要管理员二次审核。审核通过后，再执行模拟上线发布。
```

### 发起任务时的关键参数

页面选择流程模板后，前端应在任务创建请求里带上：

```json
{
  "metadata": {
    "execution_mode": "workflow_template",
    "workflow_id": "<Bug 修复上线 Workflow 的 ID>"
  }
}
```

携带这个参数后，任务仍然会进入任务清单确认弹窗；人工确认后，后续执行按模板节点流转，不应该再卡在动态分发节点。

## 验证观察点

- 任务详情的任务清单应展示 Bug 修复相关任务，而不是只重复原始诉求。
- 无流程模板模式下，任务进入人工节点依赖 LLM 分发结果；如果没产生人工节点，需要把任务诉求写得更明确。
- 有流程模板模式下，人工节点处理人以模板节点配置为准。
- 人工子任务可通过 `GET /api/v1/subtasks/human?assignee_user_id=root` 查询。
- 人工节点提交成功后，子任务状态变为 `succeeded`，主任务继续后续节点。
- 如果任一 Agent 子任务执行失败，主任务应进入 `failed`，不应继续执行后续轮次。
- 详情页里失败节点应能通过悬停查看失败原因。

