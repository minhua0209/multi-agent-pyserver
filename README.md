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
uvicorn app.main:app --reload
```

By default, agents are stored in `app/data/agents.json` and tasks are kept in
memory. To persist both agents and tasks in MySQL, create the database first and
set `DATABASE_URL` before starting the service:

```bash
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS multi_agent_pyserver DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
export DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3306/multi_agent_pyserver?charset=utf8mb4"
uvicorn app.main:app --reload
```

When `DATABASE_URL` is set, the service creates the required `agents` and
`tasks` tables automatically. The current MVP stores each agent/task as a JSON
payload, which keeps the schema stable while the task model is still changing.

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

Confirm task details. After this call, the service automatically runs the
multi-round dispatch and execution loop until no subtasks remain, execution
fails, or the loop limit is reached.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/{task_id}/confirm \
  -H "Content-Type: application/json" \
  -d '{"title":"Create a quote for customer A","description":"Prepare and send quote for customer A"}'
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
