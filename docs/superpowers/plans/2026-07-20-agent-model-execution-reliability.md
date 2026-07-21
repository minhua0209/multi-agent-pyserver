# Agent Model Execution Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real Agent model execution resilient to one transient/format failure and preserve the actual failure reason when retries are exhausted.

**Architecture:** Keep the existing urllib model client and LangGraph workflow. Add a focused Agent response parser and typed execution error in `model_client.py`, honor per-Agent retry configuration, and let `task_graph.py` decide whether to use the existing Mock fallback or surface the real error.

**Tech Stack:** Python 3.12+, Pydantic, FastAPI, LangGraph, pytest.

---

### Task 1: Agent response parsing and retries

**Files:**
- Modify: `tests/test_model_client.py`
- Modify: `app/core/model_client.py`

- [x] **Step 1: Write failing tests**

Add tests proving that a first invalid JSON response is retried according to `agent.execution_config.max_retries`, that a plain-text response becomes final output, and that exhausted retries raise `AgentModelExecutionError` with the last cause.

```python
def test_agent_execution_retries_invalid_json(monkeypatch):
    responses = iter(["{invalid", '{"tool_calls": [], "output": "完成"}'])
    monkeypatch.setattr(default_client, "create", lambda *_args: next(responses))
    tool_calls, output = execute_subtask_with_tools_model(task, subtask, retrying_agent, [])
    assert tool_calls == []
    assert output == "完成"
```

- [x] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q tests/test_model_client.py -k 'agent_execution'`

Expected: new retry/plain-text/error tests fail because the current implementation returns `None` and ignores `max_retries`.

- [x] **Step 3: Implement minimal parser and retry loop**

Add `AgentModelExecutionError`, `_parse_agent_execution_response()` and a bounded loop of `1 + max_retries` attempts. Accept non-empty plain text as output; retry malformed JSON-like responses.

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_model_client.py -k 'agent_execution'`

Expected: all selected tests pass.

### Task 2: Preserve real failure through TaskGraph

**Files:**
- Modify: `tests/test_task_graph.py`
- Modify: `app/workflows/task_graph.py`

- [x] **Step 1: Write failing graph tests**

Add one test with Mock disabled expecting the real `AgentModelExecutionError` reason, and one with Mock enabled expecting the existing Mock output.

```python
def test_task_graph_surfaces_agent_model_error_when_mock_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda *_args: (_ for _ in ()).throw(AgentModelExecutionError(2, "invalid JSON response")),
    )
    with pytest.raises(RuntimeError, match="invalid JSON response"):
        runner._execute_subtask(task, subtask, agent)
```

- [x] **Step 2: Run graph tests and verify RED**

Run: `python3 -m pytest -q tests/test_task_graph.py -k 'agent_model_error'`

Expected: exception is not yet translated according to the Mock policy.

- [x] **Step 3: Implement graph error handling**

Catch `AgentModelExecutionError`. Use Mock only when the existing system fallback flag is enabled; otherwise raise an error containing the real model cause.

- [x] **Step 4: Run graph tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_task_graph.py -k 'agent_model_error'`

Expected: selected tests pass.

### Task 3: Detect truncated chat completions

**Files:**
- Modify: `tests/test_model_client.py`
- Modify: `app/core/model_client.py`

- [x] **Step 1: Write a failing client test**

Mock a chat-completions response with `finish_reason="length"` and assert that `OpenAIResponsesClient.create()` raises `ModelCallError` mentioning truncation.

- [x] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest -q tests/test_model_client.py -k 'finish_reason_length'`

- [x] **Step 3: Implement finish-reason validation**

Inspect `choices[0].finish_reason` before returning extracted text and raise a specific error for `length`.

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_model_client.py -k 'finish_reason_length'`

### Task 4: Keep SubTask IDs within the database contract

**Files:**
- Create: `app/workflows/subtask_identity.py`
- Modify: `app/workflows/task_graph.py`
- Modify: `app/workflows/template_runner.py`
- Create: `tests/test_subtask_identity.py`

- [x] **Step 1: Reproduce the real 71-character ID failure**

Verify that the first browser E2E reaches a real model output, then fails while MySQL persists `subtasks.id VARCHAR(64)`.

- [x] **Step 2: Add failing helper and integration tests**

Cover deterministic shortening, short-ID compatibility, structured identity separation, Unicode boundaries, and both Workflow creation paths.

- [x] **Step 3: Implement the shared identity builder**

Preserve IDs at or below 64 characters and use `subtask_` plus a SHA-256 digest for overflow identities. Preserve the complete `logical_key`.

- [x] **Step 4: Run focused storage and Workflow tests**

Result: `90 passed` across the helper and related Workflow/rerun suites.

### Task 5: Full automated and real-model verification

**Files:**
- Modify: `docs/技术方案.md`

- [x] **Step 1: Run backend tests**

Run: `python3 -m pytest -q`

Expected: zero failures.

- [x] **Step 2: Restart local services with real model configuration**

Stop only the current backend listener, then start the updated app with credentials loaded in process memory. Keep `ENABLE_SYSTEM_MOCK_FALLBACK=false`.

- [x] **Step 3: Execute the page workflow**

From `http://127.0.0.1:5173`, publish `E2E-真实模型-最终验收-20260720-3`, confirm goal/deliverables/success criteria, and follow Task `task_e6c3675a5067` until completion.

- [x] **Step 4: Verify success evidence**

Verified: task and active execution are `succeeded`; the completion report is `1/1 success criteria passed`; output contains `TASKHUB-REAL-9537`; two text Artifacts are `valid`; the persisted SubTask ID is 64 characters.

- [x] **Step 5: Sync documentation and inspect diff**

Update the technical solution with retry/error semantics, then run `git diff --check` and `git status --short`. Do not commit.
