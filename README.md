# TaskHub MVP

API-only local MVP for the multi-agent task collaboration center.

The post-confirmation task flow is orchestrated with LangGraph. After human
confirmation, the main task runs as a multi-round loop: the dispatcher reads the
current task context, plans one or more subtasks, executes them, writes results
back to context, and then decides whether another round is needed. Model calls
use the local OpenAI-compatible service first, then fall back to mock behavior if
the model call fails.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
export MODEL_RESPONSES_API_URL="http://192.168.18.94:30377/v1/responses"
export MODEL_API_KEY="replace-with-your-model-api-key"
export MODEL_NAME="qwen3.6-35b"
export ENABLE_SYSTEM_MOCK_FALLBACK="false"
uvicorn app.main:app --reload
```

By default, the app connects to the local MySQL demo database:

```text
mysql+pymysql://root:demo_root_123@localhost:3306/demo_db?charset=utf8mb4
```

To override it, set `DATABASE_URL` before starting the service:

```bash
export DATABASE_URL="mysql+pymysql://root:demo_root_123@localhost:3306/demo_db?charset=utf8mb4"
uvicorn app.main:app --reload
```

When database mode is enabled, the service creates the required agent, task,
round, subtask, event, snapshot, tool execution, and workflow template tables
automatically.

`ENABLE_SYSTEM_MOCK_FALLBACK` controls only system-level fallback behavior for
intent recognition, round dispatch, agent execution, and human-node fallback.
The default is `false`: model failures surface as errors instead of silently
using local mock planning/execution. Agent tools with `type=mock` are still
available and are the recommended way to demo external tool results locally.
Set `ENABLE_SYSTEM_MOCK_FALLBACK=true` only when you want the older fully local
fallback demo behavior.

## Test

```bash
pytest -q
```

## Example Flow

Create an agent:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Quote Agent",
    "description": "Handles quote tasks and can query CRM metadata",
    "capabilities": ["quote", "crm"],
    "tools": [
      {
        "name": "crm_query",
        "description": "Query customer information from CRM",
        "type": "http",
        "config": {
          "method": "GET",
          "url": "https://crm.example.com/customers/{customer_id}"
        },
        "input_schema": {
          "type": "object",
          "properties": {
            "customer_id": {"type": "string"}
          }
        }
      }
    ]
  }'
```

Agents can also declare execution preferences and structured input/output
schemas:

```json
{
  "execution_config": {
    "system_prompt": "你是报价 agent",
    "model_name": "qwen3.6-35b",
    "temperature": 0.2,
    "timeout_seconds": 60,
    "max_retries": 2,
    "max_tool_calls": 5
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "customer_id": {"type": "string"}
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "quote_amount": {"type": "number"}
    }
  }
}
```

`tools` can be executed by the tool executor. The MVP currently supports `mock`,
basic `http`, read-only `mysql`, and `smtp_email` tools. Tool calls and tool
results are written back to `context.rounds[].subtasks[]`.

Example MySQL tool declaration:

```json
{
  "name": "customer_query",
  "description": "Query customer information from MySQL",
  "type": "mysql",
  "config": {
    "host": "127.0.0.1",
    "port": "3306",
    "user": "demo_user",
    "password": "demo_pass_123",
    "database": "demo_db",
    "query": "select customer_name, level from customers where id = '{customer_id}'",
    "max_rows": "50"
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "customer_id": {"type": "string"}
    }
  }
}
```

Example SMTP email agent declaration:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Email Agent",
    "description": "Handles sending emails to target recipients",
    "capabilities": ["email", "notification", "send_email"],
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
  }'
```

The email tool requires `to`, `subject`, and `body` in the generated tool call.
For a real send test, replace the SMTP config with a valid sender account. A
task description such as `请发送一封测试邮件给 minh@getui.com，主题为 Agent 测试邮件，正文说明这是任务协同中心发出的测试邮件。`
can be routed to an agent with email capabilities.

Create a workflow template. Templates are stored in `workflow_templates` when
`DATABASE_URL` is set; otherwise they are stored in `app/data/workflows.json`.
Updating a template overwrites the current template in place.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Quote Approval",
    "description": "Create a quote and approve it manually",
    "definition": {
      "nodes": [
        {"id": "start", "type": "start"},
        {
          "id": "make_quote",
          "type": "agent",
          "agent_id": "agent_xxx",
          "title": "Make quote",
          "description": "Create a quote draft"
        },
        {
          "id": "approve_quote",
          "type": "human",
          "title": "Approve quote",
          "description": "Approve the quote draft"
        },
        {"id": "end", "type": "end"}
      ],
      "edges": [
        {"from": "start", "to": "make_quote"},
        {"from": "make_quote", "to": "approve_quote"},
        {"from": "approve_quote", "to": "end"}
      ]
    }
  }'
```

Create a task request:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/requests \
  -H "Content-Type: application/json" \
  -d '{"source_type":"business_system","content":"Create a quote for customer A; review customer B contract risk"}'
```

Run a task with a workflow template instead of dynamic dispatch:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/requests \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "business_system",
    "content": "Create quote through workflow",
    "metadata": {
      "execution_mode": "workflow_template",
      "workflow_id": "workflow_xxx"
    }
  }'
```

The response contains one request id and one main task:

```json
{
  "request_id": "req_xxx",
  "tasks": [
    {
      "id": "task_xxx",
      "task_status": "running",
      "current_node": "human_confirmation",
      "draft": {
        "title": "Create a quote for customer A",
        "description": "Prepare quote for customer A",
        "confidence": 0.9,
        "suggested_assignee_type": "agent",
        "suggested_agent_id": "agent_xxx"
      },
      "context": {
        "summary": "",
        "rounds": []
      }
    }
  ]
}
```

Intent recognition uses the currently registered agents when preparing the main
task draft. After confirmation, the dispatcher plans subtasks round by round
from the latest `context`, so later subtasks can use previous subtask results.

Confirm task details. `execution_mode` defaults to `sync`, which keeps the old
blocking behavior. Set `execution_mode` to `async` to return immediately after
confirmation and run the multi-round dispatch loop in the background.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/{task_id}/confirm \
  -H "Content-Type: application/json" \
  -d '{"title":"Create a quote for customer A","description":"Prepare and send quote for customer A","execution_mode":"async"}'
```

Poll tasks assigned to an agent:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents/{agent_id}/poll
```

List human subtasks waiting for manual handling:

```bash
curl http://127.0.0.1:8000/api/v1/subtasks/human
```

Submit a human subtask result. If all subtasks in the current round have
finished, the service merges the round context and resumes automatic dispatch.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/subtasks/{subtask_id}/result \
  -H "Content-Type: application/json" \
  -d '{"result_status":"succeeded","output":"discount approved","should_complete":true}'
```
