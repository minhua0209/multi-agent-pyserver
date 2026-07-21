# Multi Agent PyServer API 接口文档

## 基础信息

- 服务名称：`TaskHub MVP`
- Base URL：`http://127.0.0.1:8000`
- 接口前缀：`/api/v1`
- 数据格式：`Content-Type: application/json`
- 当前鉴权：开发态通过请求头 `X-User-Id` 指定当前用户；不传时默认 `root` 管理员
- OpenAPI 文档：
  - Swagger UI：`GET /docs`
  - OpenAPI JSON：`GET /openapi.json`

## 枚举值

### `source_type`

| 值 | 含义 |
| --- | --- |
| `human` | 人工发起 |
| `business_system` | 业务系统发起 |
| `agent` | agent 发起 |

### `task_status`

| 值 | 含义 |
| --- | --- |
| `running` | 正在执行 |
| `succeeded` | 执行完成 |
| `failed` | 执行失败 |

### `current_node`

| 值 | 含义 |
| --- | --- |
| `intent_recognition` | 意图识别 |
| `human_confirmation` | 人工确认主任务 |
| `waiting_dependencies` | 等待前置任务完成 |
| `dispatch_decision` | 分发 agent 决策 |
| `subtask_execution` | 子任务执行 |
| `context_update` | 上下文更新 |
| `agent_execution` | agent 执行 |
| `human_execution` | 人工子任务执行 |
| `completion_judge` | 完成判断 |
| `human_intervention` | 人工介入 |

### `result_status`

| 值 | 含义 |
| --- | --- |
| `succeeded` | 成功 |
| `failed` | 失败 |
| `blocked` | 阻塞 |
| `partial` | 部分完成 |

### `user_role`

| 值 | 含义 |
| --- | --- |
| `admin` | 管理员 |
| `user` | 普通用户 |

## 用户与权限

### 获取当前用户

`GET /api/v1/users/current`

请求头：

```http
X-User-Id: user_xxx
```

响应示例：

```json
{
  "id": "root",
  "name": "管理员",
  "phone": "",
  "email": "",
  "role": "admin",
  "department": "平台",
  "position": "系统管理员",
  "status": "active",
  "remark": "默认管理员",
  "created_at": "2026-07-16T00:00:00Z",
  "updated_at": "2026-07-16T00:00:00Z"
}
```

### 用户管理

管理员接口：

- `GET /api/v1/users`
- `POST /api/v1/users`
- `PUT /api/v1/users/{user_id}`
- `DELETE /api/v1/users/{user_id}`

创建用户请求示例：

```json
{
  "name": "张三",
  "phone": "13800000001",
  "email": "zhangsan@example.com",
  "role": "user",
  "department": "交付部",
  "position": "交付经理",
  "status": "active",
  "remark": "负责客户交付确认"
}
```

人工节点选人接口：

- `GET /api/v1/users/assignable`

返回启用用户的简要信息，供前端下拉框展示姓名：

```json
[
  { "id": "root", "name": "管理员", "role": "admin" },
  { "id": "user_xxx", "name": "张三", "role": "user" }
]
```

权限规则：

- 管理员可以查询和操作全部菜单、任务、用户和人工节点。
- 普通用户只能查看自己发起的任务列表和任务详情。
- 普通用户只能处理分配给自己的人工节点。

## 前端典型流程

1. 创建或查询处理 agent：`POST /api/v1/agents`、`GET /api/v1/agents`
2. 业务系统发起任务：`POST /api/v1/tasks/requests`
3. 前端展示返回的 `tasks[0].draft`，让人工确认标题和描述。
4. 人工确认主任务：`POST /api/v1/tasks/{task_id}/confirm`
5. 确认后系统自动进入分发、agent 执行、工具调用、上下文更新、完成判断流程。
6. 如果任务进入人工子任务节点，前端查询：`GET /api/v1/subtasks/human`
7. 人工提交子任务结果：`POST /api/v1/subtasks/{subtask_id}/result`
8. 前端轮询或查询主任务详情：`GET /api/v1/tasks/{task_id}`

## Agent 接口

### 创建 Agent

`POST /api/v1/agents`

用于注册可被分发 agent 感知和调用的处理 agent。agent 信息会持久化到本地文件或 MySQL，取决于服务启动时是否配置 `DATABASE_URL`。

请求示例：

```json
{
  "name": "Email Agent",
  "description": "Handles sending emails to target recipients",
  "agent_type": "processing",
  "capabilities": ["email", "notification", "send_email"],
  "input_schema": {},
  "output_schema": {},
  "execution_config": {
    "system_prompt": "你是邮件发送 agent",
    "model_name": "qwen3.6-35b",
    "temperature": 0.2,
    "timeout_seconds": 60,
    "max_retries": 0,
    "max_tool_calls": 5
  },
  "tools": [
    {
      "name": "send_email",
      "description": "Send email through SMTP",
      "type": "smtp_email",
      "config": {
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "username": "sender@example.com",
        "password": "replace-with-smtp-password",
        "from": "sender@example.com",
        "use_tls": "true",
        "timeout_seconds": "30"
      },
      "input_schema": {
        "type": "object",
        "properties": {
          "to": {"type": "string"},
          "subject": {"type": "string"},
          "body": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
      }
    }
  ]
}
```

响应：`201 Created`

```json
{
  "id": "agent_xxx",
  "name": "Email Agent",
  "description": "Handles sending emails to target recipients",
  "agent_type": "processing",
  "capabilities": ["email", "notification", "send_email"],
  "input_schema": {},
  "output_schema": {},
  "execution_config": {
    "system_prompt": "你是邮件发送 agent",
    "model_name": "qwen3.6-35b",
    "temperature": 0.2,
    "timeout_seconds": 60,
    "max_retries": 0,
    "max_tool_calls": 5
  },
  "tools": [
    {
      "name": "send_email",
      "description": "Send email through SMTP",
      "type": "smtp_email",
      "config": {
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "username": "sender@example.com",
        "password": "replace-with-smtp-password",
        "from": "sender@example.com",
        "use_tls": "true",
        "timeout_seconds": "30"
      },
      "input_schema": {
        "type": "object",
        "properties": {
          "to": {"type": "string"},
          "subject": {"type": "string"},
          "body": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
      }
    }
  ],
  "created_at": "2026-07-14T00:00:00Z"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | agent 名称 |
| `description` | string | 否 | agent 描述，分发 agent 会参考 |
| `agent_type` | string | 否 | agent 类型，默认 `processing`。可用于标识 `processing`、`condition`、`system` 等类型 |
| `capabilities` | string[] | 否 | 能力标签，分发 agent 会参考 |
| `input_schema` | object | 否 | 期望输入结构 |
| `output_schema` | object | 否 | 期望输出结构 |
| `execution_config` | object | 否 | 执行配置 |
| `tools` | AgentTool[] | 否 | agent 可调用工具 |

当前支持的工具类型：

| 类型 | 说明 |
| --- | --- |
| `mock` | 返回固定 mock 响应 |
| `http` | 发起基础 HTTP 请求 |
| `mysql` | 执行只读 MySQL `SELECT` 查询 |
| `smtp_email` | 通过 SMTP 发送邮件 |
| `file_write` | 将文章、报告、总结写入本地指定目录 |

`agent_type` 说明：

- `GET /api/v1/agents` 会返回全部 agent/画布元素，便于前端流程画布使用。
- 无流程动态协同中的意图识别、LLM/CrewAI 分发和执行候选只使用 `agent_type=processing` 的 agent。
- `condition` 流程节点不要求注册为 agent；如果前端为了画布物料管理注册了非处理 agent，不会参与动态分发。

### 极简创建 Agent

`POST /api/v1/agents/simple`

用于让非专业用户通过一句自然语言诉求创建处理 agent。系统会基于内置工具目录自动生成 `AgentCreate` 参数，并继续复用现有 `agents` 表/文件持久化逻辑。

请求示例：

```json
{
  "ability": "帮我创建一个可以向指定目录写入文章或者报告总结的agent",
  "name": "报告写入助手"
}
```

创建成功响应：`201 Created`

```json
{
  "status": "created",
  "message": "已根据诉求生成 agent 参数。",
  "agent": {
    "id": "agent_xxx",
    "name": "报告写入助手",
    "description": "根据用户诉求自动生成：帮我创建一个可以向指定目录写入文章或者报告总结的agent",
    "agent_type": "processing",
    "capabilities": ["write_article", "write_report", "summarize", "save_file"],
    "execution_config": {
      "system_prompt": "你是一个将文章、报告、总结写入本地指定目录的处理 agent...",
      "model_name": "",
      "temperature": null,
      "timeout_seconds": 60,
      "max_retries": 1,
      "max_tool_calls": 5
    },
    "tools": [
      {
        "name": "file_write",
        "description": "将文章、报告、总结写入本地指定目录",
        "type": "file_write",
        "config": {
          "base_dir": "./runtime/agent_outputs"
        }
      }
    ],
    "created_at": "2026-07-15T00:00:00Z"
  },
  "matched_tools": ["file_write"],
  "missing_tools": [],
  "guidance": []
}
```

如果诉求包含多个独立能力，不会创建 agent，响应：`200 OK`

```json
{
  "status": "needs_split",
  "message": "当前诉求包含多个独立能力，建议分开创建多个 agent，或先补充对应工具后再创建。",
  "agent": null,
  "matched_tools": ["mysql", "smtp_email", "http"],
  "missing_tools": [],
  "guidance": [
    "一个处理 agent 建议只承接一类稳定能力。",
    "例如将数据库查询、邮件发送、HTTP 调用分别创建为不同 agent。"
  ]
}
```

如果诉求需要当前系统尚未支持的工具，不会创建 agent，响应：`200 OK`

```json
{
  "status": "tool_missing",
  "message": "当前诉求需要系统尚未接入的工具能力，请先补充工具或调整诉求。",
  "agent": null,
  "matched_tools": [],
  "missing_tools": [
    {
      "type": "wechat_group_sender",
      "reason": "当前系统没有企业微信或微信群消息发送工具。",
      "suggested_action": "可以接入企业微信 webhook 工具，或用 HTTP 工具配置 webhook 地址。"
    }
  ],
  "guidance": ["可以先寻找或注册对应工具，再创建处理 agent。"]
}
```

### 查询 Agent 列表

`GET /api/v1/agents`

响应：`200 OK`，以下仅展示关键字段。

```json
[
  {
    "id": "agent_xxx",
    "name": "Email Agent",
    "description": "Handles sending emails to target recipients",
    "agent_type": "processing",
    "capabilities": ["email"],
    "input_schema": {},
    "output_schema": {},
    "execution_config": {
      "system_prompt": "",
      "model_name": "",
      "temperature": null,
      "timeout_seconds": 60,
      "max_retries": 0,
      "max_tool_calls": 5
    },
    "tools": [],
    "created_at": "2026-07-14T00:00:00Z"
  }
]
```

### Agent 拉取已分配任务

`POST /api/v1/agents/{agent_id}/poll`

当前返回 `assigned_agent_id` 等于该 agent 的主任务列表。后续如果要做真正的外部 agent worker，可基于这个接口扩展。

响应：`200 OK`，以下仅展示关键字段。

```json
[
  {
    "id": "task_xxx",
    "task_status": "running",
    "current_node": "agent_execution",
    "title": "发送测试邮件",
    "description": "向 minh@getui.com 发送测试邮件"
  }
]
```

## Task 接口

### 创建任务请求

`POST /api/v1/tasks/requests`

业务系统或外部调用方通过该接口发起任务。系统会先走意图识别，生成待人工确认的主任务 draft。当前主任务确认是必经步骤，不能跳过。

请求示例：

```json
{
  "source_type": "business_system",
  "title": "发送测试邮件任务",
  "content": "请发送一封测试邮件给 minh@getui.com，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。",
  "metadata": {}
}
```

使用 workflow 模板执行时：

```json
{
  "source_type": "business_system",
  "title": "客户A报价流程",
  "content": "按报价审批流程处理客户 A 报价",
  "metadata": {
    "execution_mode": "workflow_template",
    "workflow_id": "workflow_xxx"
  }
}
```

响应：`201 Created`

```json
{
  "request_id": "req_xxx",
  "tasks": [
    {
      "id": "task_xxx",
      "request_id": "req_xxx",
      "source_type": "business_system",
      "title": "发送测试邮件任务",
      "description": "请发送一封测试邮件给 minh@getui.com，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。",
      "content": "请发送一封测试邮件给 minh@getui.com...",
      "request_metadata": {},
      "task_status": "running",
      "current_node": "human_confirmation",
      "draft": {
        "draft_key": null,
        "title": "发送测试邮件",
        "description": "向 minh@getui.com 发送测试邮件",
        "confidence": 0.8,
        "suggested_assignee_type": "agent",
        "suggested_agent_id": "agent_xxx",
        "depends_on": []
      },
      "assigned_agent_id": "agent_xxx",
      "dependency_task_ids": [],
      "context": {
        "summary": "",
        "rounds": [],
        "artifacts": []
      },
      "final_output": "",
      "loop_count": 0,
      "max_loop_count": 10,
      "events": [],
      "created_at": "2026-07-14T00:00:00Z",
      "updated_at": "2026-07-14T00:00:00Z"
    }
  ]
}
```

返回字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | string | 本次上游请求 ID |
| `tasks` | Task[] | 生成的主任务列表；当前服务会把识别出的 draft 合并成一个主任务返回 |
| `tasks[].title` | string | 任务名称，最多 50 个字 |
| `tasks[].description` | string | 任务诉求，默认与 `content` 一致 |
| `tasks[].content` | string | 原始任务诉求 |
| `tasks[].draft` | TaskDraft | 待人工确认的任务草稿 |
| `tasks[].current_node` | string | 当前节点；创建后固定进入 `human_confirmation` |
| `tasks[].assigned_agent_id` | string/null | 意图识别阶段建议的 agent |

### 人工确认主任务

`POST /api/v1/tasks/{task_id}/confirm`

前端展示 draft 后，由人工确认或修改任务标题和描述。

`execution_mode` 支持：

| 值 | 说明 |
| --- | --- |
| `sync` | 默认值。接口会同步运行后续自动流转，直到任务完成、失败、挂起到人工节点或达到循环上限后返回。 |
| `async` | 接口只完成确认并调度后台执行，然后立即返回当前任务状态。前端通过 `GET /api/v1/tasks/{task_id}` 查询后续轨迹。 |

请求示例：

```json
{
  "title": "发送测试邮件",
  "description": "向 minh@getui.com 发送测试邮件，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。",
  "execution_mode": "async"
}
```

响应：`200 OK`

返回完整 `Task`。常见结果：

- `task_status=running,current_node=dispatch_decision`：异步模式下已确认并已调度后台执行。
- `task_status=succeeded`：任务已自动执行完成。
- `task_status=running,current_node=human_execution`：任务中存在人工子任务，等待人工提交。
- `task_status=running,current_node=waiting_dependencies`：任务等待前置任务完成。
- `task_status=failed`：任务执行失败。

错误：

| HTTP 状态 | detail | 说明 |
| --- | --- | --- |
| 404 | `Task not found` | 任务不存在 |
| 404 | `Workflow not found` | 指定 workflow 模板不存在 |

### 取消未确认主任务

`DELETE /api/v1/tasks/{task_id}`

用于任务发布页意图识别完成后，人工在任务清单确认弹窗中点击取消。该接口只允许取消仍处于 `human_confirmation` 的未确认草稿任务；任务一旦确认并进入自动执行流，不允许通过该接口删除。

响应：`204 No Content`

错误：

| HTTP 状态 | detail | 说明 |
| --- | --- | --- |
| 404 | `Task not found` | 任务不存在 |
| 409 | `Only unconfirmed tasks can be cancelled` | 任务已确认或已进入执行流，不能取消 |

### 提交主任务执行结果

`POST /api/v1/tasks/{task_id}/result`

用于外部系统或人工直接提交主任务级结果。普通 agent 自动执行流程中不一定需要前端调用。

请求示例：

```json
{
  "result_status": "succeeded",
  "output": "任务已处理完成",
  "should_complete": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `result_status` | enum | 是 | `succeeded`、`failed`、`blocked`、`partial` |
| `output` | string | 否 | 执行结果文本 |
| `should_complete` | boolean | 否 | 是否直接结束主任务，默认 `true` |

响应：`200 OK`，返回完整 `Task`。

### 查询任务详情

`GET /api/v1/tasks/{task_id}`

响应：`200 OK`，返回完整 `Task`。

重点字段：

| 字段 | 说明 |
| --- | --- |
| `task_status` | 主任务状态 |
| `current_node` | 当前执行节点 |
| `context.summary` | 当前累计上下文摘要 |
| `context.rounds` | 每轮分发和执行轨迹 |
| `context.rounds[].subtasks` | 每轮子任务 |
| `subtasks[].tool_calls` | agent 生成的工具调用 |
| `subtasks[].tool_results` | 工具真实执行结果 |
| `final_output` | 主任务最终输出 |
| `events` | 任务事件流水 |

### 查询任务列表

`GET /api/v1/tasks`

响应：`200 OK`

```json
[
  {
    "id": "task_xxx",
    "task_status": "succeeded",
    "current_node": "completion_judge",
    "title": "发送测试邮件",
    "final_output": "邮件发送完成：Email sent to minh@getui.com"
  }
]
```

## Human Subtask 接口

### 查询待人工处理子任务

`GET /api/v1/subtasks/human`

返回所有 `assignee_type=human` 且 `status=running` 的子任务。

响应：`200 OK`

```json
[
  {
    "id": "subtask_xxx",
    "title": "人工审批报价",
    "description": "请确认报价金额",
    "assigned_agent_id": null,
    "assignee_type": "human",
    "current_node": "human_execution",
    "status": "running",
    "tool_calls": [],
    "tool_results": [],
    "output": ""
  }
]
```

### 提交人工子任务结果

`POST /api/v1/subtasks/{subtask_id}/result`

并行轮次中可以同时存在 agent 子任务和人工子任务。agent 子任务会先自动执行，人工子任务会挂起等待该接口提交。提交后，如果本轮所有子任务都完成，系统会合并上下文并自动恢复后续分发流程。

`execution_mode` 支持：

- `sync`：默认值。接口会等待后续自动流程执行完成后再返回。
- `async`：接口只保存人工结果并调度后台恢复流程，立即返回当前任务快照，适合前端人工确认工作台使用。

请求示例：

```json
{
  "result_status": "succeeded",
  "output": "人工确认通过",
  "should_complete": true,
  "metadata": {
    "decision": "approved"
  },
  "execution_mode": "async"
}
```

响应：`200 OK`，返回完整 `Task`。

错误：

| HTTP 状态 | detail | 说明 |
| --- | --- | --- |
| 404 | `Subtask not found` | 子任务不存在 |
| 404 | `Workflow not found` | 后续恢复流程需要的 workflow 不存在 |

## Workflow 模板接口

### 创建 Workflow 模板

`POST /api/v1/workflows`

用于保存由前端编排的 agent 节点和人工节点流程。当前模板更新采用原模板覆盖，不做版本管理。

请求示例：

```json
{
  "name": "Quote Approval",
  "description": "Create a quote and approve it manually",
  "definition": {
    "nodes": [
      {
        "id": "start",
        "type": "start",
        "title": "开始",
        "description": ""
      },
      {
        "id": "make_quote",
        "type": "agent",
        "title": "生成报价",
        "description": "生成报价草稿",
        "agent_id": "agent_xxx",
        "config": {}
      },
      {
        "id": "approve_quote",
        "type": "human",
        "title": "人工审批",
        "description": "审批报价草稿",
        "config": {}
      },
      {
        "id": "judge_approval",
        "type": "condition",
        "title": "判断审批结果",
        "description": "将人工审批结果归一化为 decision",
        "config": {
          "condition_description": "判断审批结果",
          "condition_options": [
            {
              "value": "approved",
              "content": "人工审批通过，可以继续后续流程"
            },
            {
              "value": "rejected",
              "content": "人工审批驳回，需要返工或结束"
            }
          ]
        }
      },
      {
        "id": "end",
        "type": "end",
        "title": "结束",
        "description": ""
      }
    ],
    "edges": [
      {"from": "start", "to": "make_quote", "condition": {}},
      {"from": "make_quote", "to": "approve_quote", "condition": {}},
      {"from": "approve_quote", "to": "judge_approval", "condition": {}},
      {"from": "judge_approval", "to": "end", "condition": {"type": "decision", "value": "approved"}}
    ]
  }
}
```

响应：`201 Created`

```json
{
  "id": "workflow_xxx",
  "name": "Quote Approval",
  "description": "Create a quote and approve it manually",
  "definition": {
    "nodes": [
      {
        "id": "start",
        "type": "start",
        "title": "开始",
        "description": "",
        "agent_id": null,
        "config": {}
      },
      {
        "id": "make_quote",
        "type": "agent",
        "title": "生成报价",
        "description": "生成报价草稿",
        "agent_id": "agent_xxx",
        "config": {}
      },
      {
        "id": "approve_quote",
        "type": "human",
        "title": "人工审批",
        "description": "审批报价草稿",
        "agent_id": null,
        "config": {}
      },
      {
        "id": "end",
        "type": "end",
        "title": "结束",
        "description": "",
        "agent_id": null,
        "config": {}
      }
    ],
    "edges": [
      {"from": "start", "to": "make_quote", "condition": {}},
      {"from": "make_quote", "to": "approve_quote", "condition": {}},
      {"from": "approve_quote", "to": "end", "condition": {}}
    ]
  },
  "status": "active",
  "created_at": "2026-07-14T00:00:00Z",
  "updated_at": "2026-07-14T00:00:00Z"
}
```

节点类型约定：

| 类型 | 说明 |
| --- | --- |
| `start` | 开始节点 |
| `agent` | agent 自动处理节点，需要配置 `agent_id` |
| `human` | 人工处理节点 |
| `condition` | 智能条件判断节点，基于条件内容、任务摘要和最近一轮输出生成标准 `decision` |
| `end` | 结束节点 |

条件判断节点说明：

- `condition` 节点不注册为普通 agent，不调用工具。
- 判断目标优先来自节点配置 `config.condition_options`，每个元素包含 `value` 和 `content`：
  - `value` 是条件节点可输出的标准 `decision`，也是后续条件边的匹配值。
  - `content` 是该分支的自然语言判断标准。
- 如果未配置 `condition_options`，兼容读取旧字段 `config.condition_content` 和 `config.allowed_decisions`。
- 判断数据来源限定为任务 `context.summary`、最近一轮已完成子任务的 `output` 和 `result_metadata`。
- 输出会写入子任务 `result_metadata`，核心字段为 `decision`、`reason`、`matched_source`、`confidence`。
- `decision` 必须落在 `condition_options[].value` 或兼容字段 `config.allowed_decisions` 内；模型调用失败、无法判断、返回空值或非法值时，条件子任务失败，主任务状态置为 `failed`，失败原因记录为“无法正常判断条件”。
- 后续边建议使用标准 decision 条件：

```json
{
  "from": "judge_approval",
  "to": "make_quote",
  "condition": {
    "type": "decision",
    "value": "approved"
  }
}
```

### 查询 Workflow 列表

`GET /api/v1/workflows`

响应：`200 OK`，返回 `WorkflowTemplate[]`。

### 查询 Workflow 详情

`GET /api/v1/workflows/{workflow_id}`

响应：`200 OK`，返回 `WorkflowTemplate`。

错误：

| HTTP 状态 | detail |
| --- | --- |
| 404 | `Workflow not found` |

### 更新 Workflow 模板

`PUT /api/v1/workflows/{workflow_id}`

请求体同创建接口。当前会直接覆盖原模板定义。

响应：`200 OK`，返回更新后的 `WorkflowTemplate`。

错误：

| HTTP 状态 | detail |
| --- | --- |
| 404 | `Workflow not found` |

## Task 数据结构

```json
{
  "id": "task_xxx",
  "request_id": "req_xxx",
  "source_type": "business_system",
  "content": "原始任务内容",
  "request_metadata": {},
  "task_status": "running",
  "current_node": "human_confirmation",
  "draft": {
    "draft_key": null,
    "title": "任务标题",
    "description": "任务描述",
    "confidence": 0.8,
    "suggested_assignee_type": "agent",
    "suggested_agent_id": "agent_xxx",
    "depends_on": []
  },
  "title": "人工确认后的标题",
  "description": "人工确认后的描述",
  "assigned_agent_id": "agent_xxx",
  "dependency_task_ids": [],
  "context": {
    "summary": "累计上下文",
    "rounds": [
      {
        "round_index": 1,
        "execution_mode": "parallel",
        "reason": "Need email notification",
        "context_before": "",
        "subtasks": [
          {
            "id": "subtask_xxx",
            "title": "发送测试邮件",
            "description": "向 minh@getui.com 发送测试邮件",
            "assigned_agent_id": "agent_xxx",
            "assignee_type": "agent",
            "current_node": "agent_execution",
            "status": "succeeded",
            "tool_calls": [
              {
                "tool_name": "send_email",
                "arguments": {
                  "to": "minh@getui.com",
                  "subject": "Agent 测试邮件",
                  "body": "这是任务协同中心发出的测试邮件。"
                }
              }
            ],
            "tool_results": [
              {
                "tool_name": "send_email",
                "arguments": {
                  "to": "minh@getui.com",
                  "subject": "Agent 测试邮件",
                  "body": "这是任务协同中心发出的测试邮件。"
                },
                "success": true,
                "result": "Email sent to minh@getui.com",
                "error": ""
              }
            ],
            "output": "邮件发送完成"
          }
        ],
        "context_after": "邮件发送完成"
      }
    ],
    "artifacts": []
  },
  "final_output": "邮件发送完成",
  "loop_count": 1,
  "max_loop_count": 10,
  "events": [
    {
      "type": "task_created",
      "message": "Main task created from request req_xxx",
      "created_at": "2026-07-14T00:00:00Z"
    }
  ],
  "created_at": "2026-07-14T00:00:00Z",
  "updated_at": "2026-07-14T00:00:00Z"
}
```

## 前端对接注意事项

- `POST /api/v1/tasks/requests` 后一定要走人工确认接口，后续自动流转由服务端完成。
- 任务是否结束看 `task_status`，当前所处节点看 `current_node`。
- 如果 `task_status=running` 且 `current_node=human_execution`，前端需要展示人工子任务列表并允许提交结果。
- 子任务执行轨迹在 `context.rounds[].subtasks[]` 中，工具调用和工具结果都在子任务内。
- `/` 根路径当前没有页面或接口，返回 `{"detail":"Not Found"}` 是正常现象。
- 真实邮件发送需要先创建带 `smtp_email` 工具且 SMTP 配置可用的 Email Agent。
