---
name: taskhub-codex
description: Use when Codex needs to interact with the local TaskHub task collaboration center: publish a task, query tasks, confirm a task for execution, inspect delegated human subtasks, or run/operate the local TaskHub Codex runner that handles human-node delegation and writes results back to TaskHub.
---

# TaskHub Codex Skill

Use this skill when the user wants Codex to work with the TaskHub task collaboration center.

TaskHub has two integration modes:

1. **Publish tasks through Codex**: Codex creates a TaskHub task request from the user's natural-language task.
2. **Delegate human-node work to local Codex**: a local runner polls TaskHub human subtasks, asks Codex to produce a human decision, and writes the result back.

## Configuration

The TaskHub base URL is supplied by the local runner startup command. Do not hard-code or guess it in Codex.

The runner persists the latest startup values into `taskhub_runtime.json` in this skill directory. Before calling TaskHub APIs, read that file and use:

```json
{
  "server_url": "http://192.168.170.18:8000",
  "user_id": "王大锤",
  "runner_id": "local-codex-runner"
}
```

Use `server_url` from `taskhub_runtime.json` as the TaskHub base URL. This is required because a separate Codex session does not inherit environment variables from the runner process.

If `taskhub_runtime.json` is missing, ask the user to restart the runner with an explicit TaskHub URL and skill update enabled.

Example runner startup:

```bash
taskhub-codex-runner/start_runner.sh http://192.168.170.18:8000 王大锤
```

`127.0.0.1` is valid only when the runner/Codex process and TaskHub service share the same host network.

Before publishing or confirming tasks, verify connectivity with:

```http
GET {server_url}/api/v1/tasks
```

If the request fails, ask the user to restart the runner with a reachable TaskHub URL.

The runner may also set environment variables for its own process, but independent Codex sessions should prefer `taskhub_runtime.json`.

Useful runner environment variables:

```text
TASKHUB_SERVER_URL=<provided-by-runner-startup>
TASKHUB_USER_ID=王大锤
TASKHUB_RUNNER_ID=local-codex-runner
TASKHUB_CODEX_COMMAND="codex exec"
```

## Publish A Task

When the user asks Codex to publish a task to TaskHub, call:

```http
POST /api/v1/tasks/requests
```

Payload shape:

```json
{
  "source_type": "business_system",
  "title": "50字以内任务名称",
  "content": "用户的完整任务诉求",
  "metadata": {}
}
```

Rules:

- `title` must be 50 characters or fewer.
- `content` should be the user's business goal, not an internal workflow script.
- Preserve the user's original wording as much as possible. Do not rewrite a natural request into rigid round-by-round workflow instructions unless the user explicitly asks for that.
- If `POST /api/v1/tasks/requests` returns a server error, do not keep changing the user's business request and retrying. Report the TaskHub server error and suggest checking backend intent-recognition/model logs.
- If the user selected a workflow template, include:

```json
{
  "metadata": {
    "execution_mode": "workflow_template",
    "workflow_id": "workflow_xxx"
  }
}
```

TaskHub returns draft tasks and waits for manual task-list confirmation.

After creating a task request, do not immediately confirm it unless the user explicitly asked for direct execution before the request was created.

Instead, show the returned draft task list to the user and ask for confirmation in the Codex conversation.

Important field mapping:

- Display `task.draft.title` as the recognized task-list title.
- Display `task.draft.description` as the recognized task-list details.
- Do not use top-level `task.title`, `task.description`, or `task.content` as the task-list display when `task.draft` exists.
- Top-level `task.title` is the user-submitted request title, not the recognized task list.
- Top-level `task.content` / `task.description` is the original request, not the recognized task list.
- Only fall back to top-level fields if `task.draft` is missing.
- Never show the confirmation item as `ID / 名称 / 描述` when `draft` exists, because that usually leads to displaying the original user request instead of the recognized task list.
- The confirmation text must include the literal task-list details from `task.draft.description`, including every bullet line returned by TaskHub.

Use this confirmation format:

```text
我已从任务中心识别出以下任务清单：

任务 1
任务ID：task_xxx
任务清单标题：{task.draft.title}
任务清单明细：
{task.draft.description}

请确认如何处理：
1. 确认并执行
2. 修改任务名称
3. 修改任务描述
4. 取消任务
```

If the user confirms, call:

```http
POST /api/v1/tasks/{task_id}/confirm
```

Payload:

```json
{
  "title": "确认后的 task.draft.title",
  "description": "确认后的 task.draft.description",
  "execution_mode": "async"
}
```

If the user edits the task-list title or details, use the edited values in the confirm payload.

If the user cancels, call:

```http
DELETE /api/v1/tasks/{task_id}
```

If multiple draft tasks are returned, show every draft and ask the user to confirm, edit, or cancel each one. Confirm only the tasks the user approved.

Never skip this confirmation step for normal task publishing. The Codex chat confirmation replaces the frontend task-list confirmation modal.

## Query Tasks

Use:

```http
GET /api/v1/tasks
GET /api/v1/tasks/{task_id}
```

Prefer summarizing:

- `task_status`
- `current_node`
- `loop_count`
- latest events
- rounds and subtasks

## Handle Delegated Human Subtasks

Human-node delegation is not normal Agent task distribution.

Correct model:

```text
TaskHub human node waits for assignee
-> local runner polls human subtasks for that assignee
-> Codex assists with the human decision
-> runner submits the human result
-> TaskHub resumes the workflow
```

Poll human subtasks:

```http
GET /api/v1/subtasks/human?assignee_user_id=王大锤
```

Submit a human result:

```http
POST /api/v1/subtasks/{subtask_id}/result
```

Payload:

```json
{
  "result_status": "succeeded",
  "output": "人工处理意见",
  "should_complete": true,
  "metadata": {
    "decision": "approved",
    "handled_by": "local_codex",
    "runner_id": "local-codex-runner"
  },
  "execution_mode": "async"
}
```

Allowed `metadata.decision` values:

- `approved`: approve and continue.
- `rejected`: reject and let workflow route to rework if configured.
- `need_more_info`: information is insufficient.

## Run Local Runner

From the project root:

```bash
python3 taskhub-codex-runner/taskhub_codex_runner.py \
  --config taskhub-codex-runner/config.example.json \
  --once
```

Dry-run prompt only:

```bash
python3 taskhub-codex-runner/taskhub_codex_runner.py \
  --config taskhub-codex-runner/config.example.json \
  --once \
  --dry-run
```

The runner currently uses existing TaskHub APIs and keeps claim state locally. It does not require a server-side claim, heartbeat, or log endpoint yet.

## Decision Prompt Contract

When Codex is asked to handle a human subtask, output only JSON:

```json
{
  "action": "submit|needs_human|failed",
  "decision": "approved|rejected|need_more_info",
  "output": "人工处理意见",
  "questions": []
}
```

Do not output Markdown around this JSON.

The runner auto-submits only when:

- `action` is `submit`
- `decision` is `approved` or `rejected`

If `action` is `needs_human` or `failed`, or if `decision` is `need_more_info`, the runner must ask the local human in the terminal before writing back to TaskHub.

## Safety

- Do not auto-submit high-risk human decisions unless the user explicitly enabled auto-submit or asked you to submit.
- If the context does not justify approval or rejection, use `need_more_info`.
- For rejected decisions, include a concise reason and the required rework direction in `output`.
