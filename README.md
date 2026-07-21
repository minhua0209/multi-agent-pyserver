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
export MODEL_RESPONSES_API_URL="http://<model-host>:<port>/v1/responses"
export MODEL_API_KEY="<your-model-api-key>"
export MODEL_NAME="qwen3.6-35b"
export ENABLE_SYSTEM_MOCK_FALLBACK="false"
export TASK_PLANNER_TYPE="llm"
uvicorn app.main:app --reload
```

`TASK_PLANNER_TYPE` controls how the dispatch planner creates the next
`RoundPlan`:

- `llm`: default single-planner prompt path.
- `crewai`: optional CrewAI-based planner. Install with `pip install -e ".[crewai]"`.

CrewAI only replaces the round planning decision. LangGraph still owns task
state transitions, human-node pause/resume, context merge, and MySQL
persistence.

Database persistence is enabled when `DATABASE_URL` is provided:

```text
mysql+pymysql://<user>:<password>@localhost:3306/<database>?charset=utf8mb4
```

Set `DATABASE_URL` before starting the service:

```bash
export DATABASE_URL="mysql+pymysql://<user>:<password>@localhost:3306/<database>?charset=utf8mb4"
uvicorn app.main:app --reload
```

For Docker Compose, copy `.env.example` to `.env` and fill in local values.
`.env` is ignored by git and must not be committed.

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
