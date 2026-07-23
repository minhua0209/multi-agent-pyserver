# Criteria-Only Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Do not create Git commits; repository instructions forbid proactive commits.

**Goal:** Make visible success criteria the only business completion gate while preserving explicit execution failures and backward-compatible contract fields.

**Architecture:** Confirmation normalizes legacy delivery and human-acceptance fields to inert compatibility values and merges deliverable requirements into visible success criteria. Completion evaluates success criteria plus explicit execution integrity only; file metadata, artifact presence, deliverable requirements, and independent human acceptance no longer change task status. The frontend displays and submits only goals and success criteria.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React, TypeScript, Vitest.

---

### Task 1: Normalize confirmation into criteria-only contracts

**Files:**
- Modify: `app/core/models.py`
- Modify: `app/core/model_client.py`
- Modify: `app/services/task_contract_service.py`
- Modify: `app/services/task_service.py`
- Test: `tests/test_model_client.py`
- Test: `tests/test_tasks.py`

- [ ] Add a failing model-client test proving model suggestions for file delivery and human acceptance are ignored, while `deliverable_requirements` are merged into `success_criteria`.
- [ ] Add a failing task API test confirming a payload containing `file/text/report.patch` is accepted and stored as `text/null/empty`, with `requires_human_acceptance=false` and visible merged criteria.
- [ ] Run the two tests and confirm they fail against the current behavior.
- [ ] Normalize nested `TaskConfirm.contract` compatibility fields before `TaskContractInput` validation:

```python
contract["deliverable_kind"] = "text"
contract["deliverable_format"] = None
contract["deliverable_filename"] = ""
contract["requires_human_acceptance"] = False
```

- [ ] In `TaskContractService.confirm_contract()`, deduplicate `deliverable_requirements + success_criteria`, store the first ten as `success_criteria`, and store an empty `deliverable_requirements` list.
- [ ] Make model recognition and draft merging produce the same criteria-only defaults so hidden suggestions never reach the UI.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Remove hidden completion gates

**Files:**
- Modify: `app/services/completion_service.py`
- Test: `tests/test_completion_service.py`
- Test: `tests/test_task_graph.py`

- [ ] Add failing tests proving a task succeeds when all success criteria pass even if it has legacy file metadata, failed deliverable requirement evaluation, no selected artifact, or `requires_human_acceptance=true`.
- [ ] Add a failing test proving an explicit `candidate_status=BLOCKED` remains blocked rather than becoming an implicit human-review state.
- [ ] Run the focused tests and confirm they fail.
- [ ] Stop calling the deliverable evaluator and file-delivery gap checker from `finalize()`.
- [ ] Remove artifact presence, artifact validation gaps, deliverable results, and independent human acceptance from `_evaluate_success()` gaps.
- [ ] Preserve explicit execution-integrity gates: non-empty output, no blocking subtasks, workflow end reached, contract present, and every visible success criterion passed.
- [ ] Keep failed, blocked, partial, and cancelled execution results explicit and terminal.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Make the confirmation UI criteria-only

**Files:**
- Modify: `frontend/src/taskConfirmation.ts`
- Modify: `frontend/src/TaskConfirmationModal.tsx`
- Test: `frontend/src/taskConfirmation.test.ts`

- [ ] Add a failing regression test using `file/text/bug1_fix_code.patch` and `requires_human_acceptance=true`; validation must return no hidden-field error and the payload must contain inert compatibility defaults.
- [ ] Run the focused Vitest file and confirm it fails.
- [ ] Initialize and submit delivery compatibility fields as `text/null/empty`, always submit `requires_human_acceptance=false`, and validate only goal, deliverable goal, and success criteria.
- [ ] Remove the independent human-acceptance switch from the modal.
- [ ] Run the focused Vitest file and confirm it passes.

### Task 4: Verify behavior end to end

**Files:**
- No production changes expected.

- [ ] Run focused backend tests for models, tasks, completion, and graph behavior.
- [ ] Run the complete backend test suite with `pytest -q`.
- [ ] Run the complete frontend suite and production build.
- [ ] Restart or use an isolated local API instance with model calls mocked, create a task whose draft contains the original bad delivery triple, confirm it, and verify no extension error occurs.
- [ ] Complete the task with passed success criteria and verify the final state is `succeeded` without a file, deliverable, artifact, or human-acceptance gate.
- [ ] Check `git diff --check` and review the final diff for unrelated changes.
