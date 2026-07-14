# Agent 能力扩展验证用例

## 用例文件

- `tests/test_agents.py`
- `tests/test_task_graph.py`
- `tests/test_tool_executor.py`

## 验证目标

验证 agent 可以声明执行配置、输入/输出 schema，并支持只读 MySQL 工具调用和 SMTP 邮件发送工具调用。

## 覆盖用例

### `test_create_agent_accepts_execution_config_and_io_schema`

验证点：

- `POST /api/v1/agents` 支持 `execution_config`。
- `execution_config` 支持：
  - `system_prompt`
  - `model_name`
  - `temperature`
  - `timeout_seconds`
  - `max_retries`
  - `max_tool_calls`
- agent 支持 `input_schema` 和 `output_schema`。
- API 返回值和本地持久化会保留这些字段。

### `test_tool_executor_runs_mysql_tool`

验证点：

- `ToolExecutor` 支持 `type=mysql` 工具。
- MySQL 工具从 `tool.config.query` 读取 SQL 模板。
- 工具调用参数可以替换 SQL 模板中的占位符。
- 当前只允许执行 `SELECT` 查询。
- 查询结果会转为 JSON 数组写入 `ToolExecutionResult.result`。

### `test_tool_executor_runs_smtp_email_tool`

验证点：

- `ToolExecutor` 支持 `type=smtp_email` 工具。
- 邮件工具从 `tool.config` 读取 SMTP 主机、端口、账号、密码、发件人、TLS 和超时配置。
- 工具调用参数支持：
  - `to`
  - `subject`
  - `body`
- 执行成功后通过 SMTP `send_message` 发送邮件。
- 工具执行结果返回 `Email sent to {to}`。

### `test_tool_executor_rejects_smtp_email_tool_without_required_fields`

验证点：

- 邮件工具缺少 `to`、`subject` 或 `body` 时不会连接 SMTP。
- 工具执行结果返回失败，并明确提示缺失字段。

### `test_task_graph_routes_email_subtask_to_smtp_tool`

验证点：

- 分发计划可以将邮件子任务指定给 Email Agent。
- 处理 agent 生成 `send_email` 工具调用后，任务流转会真实调用 `ToolExecutor`。
- SMTP 邮件发送结果写回 `subtask.tool_results`。
- 工具结果会再次交给处理 agent 生成最终子任务输出。

## 最近验证结果

```text
41 passed, 1 warning
```
