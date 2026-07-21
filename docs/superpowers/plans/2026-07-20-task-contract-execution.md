# Task Contract And Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. All implementation follows TDD. Per repository instructions, do not create commits.

**Goal:** Make every task execution record its creator/trigger, confirmed goal, required deliverables, success criteria, actual artifacts, and structured completion reason, while adding backward-compatible full-task reruns.

**Architecture:** Keep `Task` as the aggregate and current-execution compatibility projection. Add `TaskContract`, immutable `TaskExecution` history, `CompletionReport`, and `Artifact`; route every terminal transition through `CompletionService`. Store the first version in the existing Task JSON payload, then expose execution APIs without introducing destructive database migrations.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, LangGraph, SQLAlchemy, React 19, TypeScript, Ant Design, Vitest, pytest, unittest.

---

### Task 1: Domain Models And Contract Confirmation

**Files:**
- Modify: `app/core/enums.py`
- Modify: `app/core/models.py`
- Create: `app/services/task_contract_service.py`
- Modify: `app/services/task_service.py`
- Modify: `app/api/routes_tasks.py`
- Modify: `app/core/model_client.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_model_client.py`
- Test: `tests/test_database_storage.py`

- [x] Write failing tests proving a confirmed task records goal, deliverable requirements, success criteria, confirmation user, and contract snapshot.
- [x] Run targeted tests and confirm they fail because contract models and fields are absent.
- [x] Add backward-compatible contract models and confirmation handling. Legacy clients receive an explicitly marked inferred contract.
- [x] Extend intent recognition output with optional suggested contract fields.
- [x] Run targeted tests and the backend suite.

### Task 2: Execution History And Completion Gate

**Files:**
- Modify: `app/core/models.py`
- Create: `app/services/execution_service.py`
- Create: `app/services/completion_service.py`
- Modify: `app/services/task_service.py`
- Modify: `app/workflows/task_graph.py`
- Modify: `app/workflows/template_runner.py`
- Test: `tests/test_completion_service.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_task_graph.py`
- Test: `tests/test_workflows.py`

- [x] Write failing tests for initial execution creation, blocked/partial preservation, empty-output rejection, Workflow end-node enforcement, and structured completion reasons.
- [x] Run targeted tests and confirm expected failures.
- [x] Add `TaskExecution`, `CompletionReport`, criterion result models, and `CompletionService`.
- [x] Replace direct terminal status writes with completion candidates handled by `CompletionService`.
- [x] Keep top-level Task status/context/final output synchronized as a compatibility projection.
- [x] Run targeted tests and the backend suite.

### Task 3: Structured Artifacts And Full Rerun API

**Files:**
- Create: `app/services/artifact_service.py`
- Create: `app/api/routes_executions.py`
- Modify: `app/services/tool_executor.py`
- Modify: `app/services/task_service.py`
- Modify: `app/workflows/template_runner.py`
- Modify: `app/main.py`
- Modify: `app/services/storage.py`
- Test: `tests/test_artifacts.py`
- Test: `tests/test_task_rerun.py`
- Test: `tests/test_database_storage.py`

- [x] Write failing tests proving input attachments are not output artifacts, file results register artifacts, and reruns preserve old executions.
- [x] Write failing tests for rerun preflight blockers, required reason, active-execution conflict, idempotency, and Workflow subtask identity isolation.
- [x] Run targeted tests and confirm expected failures.
- [x] Add artifact registration and execution list/detail/preflight/create endpoints.
- [x] Implement full rerun only; node rerun and subtask retry remain out of scope.
- [x] Run targeted tests and the backend suite.

### Task 4: Frontend Confirmation, Four Questions, And Rerun

**Files:**
- Modify: `frontend/src/api/taskhub.ts`
- Create: `frontend/src/taskContract.ts`
- Create: `frontend/src/taskExecutionView.ts`
- Create: `frontend/src/TaskConfirmationModal.tsx`
- Modify: `frontend/src/taskDetailView.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Test: `frontend/src/api/taskhub.test.ts`
- Test: `frontend/src/taskDetailView.test.ts`
- Create: `frontend/src/taskContract.test.ts`
- Create: `frontend/src/taskExecutionView.test.ts`

- [x] Write failing Vitest cases for contract payloads, four-question view models, legacy display, execution history mapping, and rerun preflight requests.
- [x] Run targeted frontend tests and confirm expected failures.
- [x] Add contract editing to the intent confirmation flow, including the manual Workflow path.
- [x] Add task detail sections for creator/trigger, goal, deliverable, completion reason, artifacts, and execution history.
- [x] Add full-rerun preflight dialog and refresh behavior.
- [x] Run frontend tests and production build.

### Task 5: Runner Identity Compatibility

**Files:**
- Modify: `taskhub-codex-runner/taskhub_codex_runner.py`
- Modify: `taskhub-codex-runner/runner_cli.py`
- Modify: `taskhub-codex-runner/config.example.json`
- Modify: `taskhub-codex-runner/README.md`
- Test: `taskhub-codex-runner/test_taskhub_codex_runner.py`
- Test: `taskhub-codex-runner/test_runner_cli.py`

- [x] Write failing tests proving all Runner API calls include `X-User-Id` and startup identity validation rejects mismatches.
- [x] Run Runner tests and confirm expected failures.
- [x] Pass the configured real user ID through `TaskHubClient` and validate `/api/v1/users/current` at startup.
- [x] Update examples so `user_id` means backend User ID, not display name.
- [x] Run Runner tests.

### Task 6: Integration Verification

**Files:**
- Modify only files required by failing integration checks.
- Update: `docs/技术方案.md` only when implemented behavior differs from its current/target labels.

- [x] Run `python3 -m pytest -q`.
- [x] Run `npm test -- --run` and `npm run build` in `frontend/`.
- [x] Run `python3 -m unittest discover -s taskhub-codex-runner -p 'test_*.py'`.
- [x] Run `git diff --check` and inspect `git status --short`.
- [x] Review that no secret value, generated runtime file, or unrelated change entered the diff.
