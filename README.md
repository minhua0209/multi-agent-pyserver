# TaskHub MVP

API-only local MVP for the multi-agent task collaboration center.

The post-confirmation task flow is orchestrated with LangGraph. After human
confirmation, the main task runs as a multi-round loop: the dispatcher reads the
current task context, plans one or more subtasks, executes them, writes results
back to context, and then decides whether another round is needed. System-level
mock fallback is disabled by default; agent tools can still use `type=mock` for
local demo data.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
export MODEL_RESPONSES_API_URL="https://model.example.com/v1/responses"
export MODEL_API_KEY="replace-with-model-api-key"
export MODEL_NAME="qwen3.6-35b"
export MODEL_MAX_OUTPUT_TOKENS="1024000"
export AGENT_OUTPUT_DIR="./runtime/agent_outputs"
export ENABLE_SYSTEM_MOCK_FALLBACK="false"
export TASK_PLANNER_TYPE="llm"
uvicorn app.main:app --reload
```

`MODEL_RESPONSES_API_URL` should be set explicitly for the model service you
intend to use. The code default is a credential-free loopback placeholder and
only works when a compatible local service is listening there.

`TASK_PLANNER_TYPE` controls how the dispatch planner creates the next
`RoundPlan`:

- `llm`: default single-planner prompt path.
- `crewai`: optional CrewAI-based planner. Install with `pip install -e ".[crewai]"`.

CrewAI only replaces the round planning decision. LangGraph still owns task
state transitions, human-node pause/resume, context merge, and MySQL
persistence.

Set `DATABASE_URL` explicitly to enable database storage:

```bash
export DATABASE_URL="mysql+pymysql://<user>:<password>@<host>:3306/<database>?charset=utf8mb4"
uvicorn app.main:app --reload
```

For Docker Compose, copy `.env.example` to `.env` and fill in local values.
`.env` is ignored by git and must not be committed.

When database mode is enabled, the service creates the required agent, task,
round, subtask, event, snapshot, tool execution, and workflow template tables
automatically.

When `DATABASE_URL` is unset, the service does not attempt a database
connection. Agents, workflows, users, and attachments use their local JSON
registries, while tasks use in-memory storage and are lost when the process
restarts. `DISABLE_DEFAULT_DATABASE_URL` remains accepted for compatibility,
but this distribution has no built-in database URL.

## Managed file delivery

Relative `AGENT_OUTPUT_DIR` values resolve from the project root. Managed files
are written to `<root>/<task_id>/<execution_id>/<filename>`. Markdown (`.md`)
and plain text (`.txt`) are supported.

After file delivery is confirmed, completion requires a managed `FILE` Artifact
for the current execution whose path is inside the output root and whose file is
non-empty. Its extension and MIME type must match the confirmed format, and its
checksum must match both the file and stored body snapshot. The system writes
the final body deterministically and atomically; the model returns the document
body directly and does not depend on `file_write`. Each rerun uses a separate
execution directory.

`MODEL_MAX_OUTPUT_TOKENS` sets the maximum output-token budget requested from
the model. The current example value is `1024000`.

`ENABLE_SYSTEM_MOCK_FALLBACK` controls only system-level fallback behavior for
intent recognition, round dispatch, agent execution, and human-node fallback.
The default is `false`: model failures surface as errors instead of silently
using local mock planning/execution. Agent tools with `type=mock` are still
available and are the recommended way to demo external tool results locally.
Set `ENABLE_SYSTEM_MOCK_FALLBACK=true` only when you want the older fully local
fallback demo behavior.
