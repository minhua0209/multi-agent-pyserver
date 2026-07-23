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

Legacy file-delivery contract fields and managed `FILE` artifacts remain
supported as compatibility metadata and completion evidence. Artifact paths,
content, and checksums are still validated, and invalid or unsafe artifacts are
filtered from selected evidence. These checks do not independently block task
completion.

Legacy `deliverable_requirements` are promoted, in their original order, into
the visible success criteria. Descriptions are deduplicated case-insensitively,
and criterion IDs are made unique. If more than ten distinct conditions remain,
the first nine stay separate and every remaining condition is included in one
visible aggregate criterion as item ten; no acceptance condition is silently
dropped. On service startup, persisted top-level contracts are normalized and
old system-generated pending-acceptance reports are reevaluated from their
stored output, criterion evidence, artifact IDs and validation states, and
workflow-end evidence. This startup reevaluation reuses stored evidence: it
does not materialize or write delivery files, register task-output artifacts,
or revalidate, replace, or invalidate artifacts. It only updates the normalized
top-level contract and the completion status, report, execution projection, and
migration event. Historical execution contract snapshots remain unchanged for
auditability; new reruns use the normalized contract. Missing promoted-
criterion evidence becomes an explicit human-adjudication gap instead of being
silently ignored.

The startup migration has no cross-process coordination. During a deployment
that may contain legacy tasks, start one service instance until its startup
migration completes, then scale out additional instances.

A successful result is determined by the visible success criteria plus
execution integrity: non-empty output, no blocking subtasks, a confirmed
contract, and (for manual workflows) reaching an end node. Explicit failed,
blocked, partial, and cancelled results remain terminal. The system can still
write legacy managed-file bodies deterministically and atomically, and each
rerun uses a separate execution directory.

`MODEL_MAX_OUTPUT_TOKENS` sets the maximum output-token budget requested from
the model. The current example value is `1024000`.

`ENABLE_SYSTEM_MOCK_FALLBACK` controls only system-level fallback behavior for
intent recognition, round dispatch, agent execution, and human-node fallback.
The default is `false`: model failures surface as errors instead of silently
using local mock planning/execution. Agent tools with `type=mock` are still
available and are the recommended way to demo external tool results locally.
Set `ENABLE_SYSTEM_MOCK_FALLBACK=true` only when you want the older fully local
fallback demo behavior.
