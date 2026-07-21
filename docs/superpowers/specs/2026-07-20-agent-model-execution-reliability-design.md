# Agent 模型执行可靠性设计

## 背景

任务 `task_fa6a5fb929a1` 和 `task_e91fc078dfb2` 均已完成意图识别、人工确认和 Agent 分配，但在首次 Agent 执行时以 `System mock fallback is disabled at agent_execution` 失败。失败 Agent 已配置 `max_retries=1`，当前执行代码却只调用模型一次，并把 HTTP、空响应和 JSON 解析异常统一转换成 `None`。

使用相同任务、Agent 和真实模型配置进行无工具副作用重放后，模型成功返回了合法 JSON、`file_write` 工具调用及报告摘要。这证明模型和 Agent 配置可用，当前缺陷是执行层没有利用重试配置，也没有保留原始失败原因。

## 目标

1. Agent 模型执行使用 `execution_config.max_retries`，一次瞬时失败不再直接终止任务。
2. 兼容模型直接返回纯文本的情况，将其作为无工具调用的最终输出。
3. JSON 响应必须满足 `tool_calls + output` 协议；截断或非法 JSON 可以重试。
4. 重试耗尽后保留脱敏后的真实模型错误，不再用 Mock 开关错误覆盖根因。
5. `ENABLE_SYSTEM_MOCK_FALLBACK=true` 时保持现有本地演示回退能力。
6. 从页面创建并确认一条真实任务，追踪到成功终态，验证真实模型输出和 Artifact。

## 非目标

- 不启用 Mock 作为生产修复。
- 不重构全部模型调用或引入新的模型 SDK。
- 不修改任务契约、执行历史或前端页面结构。
- 不增加节点级重跑。

## 设计

### 响应解析

新增专用 Agent 响应解析函数：

- 合法 JSON：解析 `tool_calls` 和 `output`。
- 非 JSON 的非空文本：视为最终文本输出，`tool_calls=[]`。
- 看起来是 JSON 或 Markdown JSON 代码块但无法解析：判定为协议错误，允许重试。
- JSON 缺少协议字段、`tool_calls` 类型错误或 `output` 非字符串：判定为协议错误。

### 重试

`execute_subtask_with_tools_model()` 最多执行 `1 + max_retries` 次。`max_retries` 的配置范围为 `0..3`，执行时再次钳制，避免异常配置放大模型请求。HTTP 错误、空响应和协议错误都可重试。每次调用仍使用现有模型客户端，不增加隐藏的跨任务重试状态。

### 错误透传

新增 `AgentModelExecutionError`，记录尝试次数和最后一次脱敏错误。重试耗尽后抛出该异常。

`TaskGraphRunner` 捕获该异常：

- Mock 回退开启：沿用现有 Mock 输出。
- Mock 回退关闭：抛出包含真实原因的运行错误，例如 `Agent model execution failed after 2 attempts: invalid JSON response`。

旧测试或扩展代码显式返回 `None` 时仍保留现有兼容分支。

### 截断识别

模型响应包含 `finish_reason=length` 时，客户端抛出明确的 `ModelCallError`，而不是把不完整内容交给 JSON 解析器。

## 验证

1. 单元测试覆盖首次失败后重试成功、纯文本响应、非法 JSON 重试耗尽、真实错误透传和截断识别。
2. 后端全量 pytest 通过。
3. 使用真实模型配置重启本地后端，保持 Mock 回退关闭。
4. 通过前端页面创建单 Agent 需求分析任务、确认契约并轮询执行。
5. 仅在任务终态为 `succeeded`、执行历史成功、输出不是 Mock 文本且存在真实模型生成内容/Artifact 时判定验收通过。

## 安全

模型凭据只在进程内从现有本地配置加载，不写入新文件、不打印、不放入命令行。日志和任务失败原因不得包含 Authorization Header 或完整请求体。

## 实施中发现的次生问题

第一次页面验收时，真实模型已经成功返回带唯一标识的需求分析结果，但执行级 SubTask ID 长度为 71，超过 MySQL `subtasks.id` 的 64 字符限制，后台持久化失败。

新增 `app/workflows/subtask_identity.py` 作为统一身份生成入口。短 ID 保持旧格式；超长 ID 使用结构化三元组的 SHA-256 摘要压缩到 64 字符，完整 Workflow 节点键继续保存在 `logical_key`。自动规划与模板 Workflow 均改用该 helper。

## 最终验收结果

- 后端全量测试：`341 passed`。
- 页面任务：`E2E-真实模型-最终验收-20260720-3`。
- Task ID：`task_e6c3675a5067`。
- Execution ID：`execution_1be4e36244cc`。
- 任务与执行终态：`succeeded`。
- 完成报告：`1/1 success criteria passed`，结束节点为 `end`。
- 真实输出包含 `TASKHUB-REAL-9537`，两份文本 Artifact 均为 `valid`。
- 持久化 SubTask ID 长度为 64，未再触发数据库长度错误。
