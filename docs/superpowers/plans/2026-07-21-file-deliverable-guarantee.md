# File Deliverable Guarantee Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a task confirmed as a Markdown/TXT file delivery succeeds only after a non-empty, checksummed document is saved under `runtime/agent_outputs/<task>/<execution>/` and registered as a current FILE Artifact.

**Architecture:** Add explicit delivery fields to the confirmed contract and pass the contract to planners and execution Agents. The model returns the final document as plain text rather than a long JSON tool argument; a focused `DeliverableMaterializer` writes it atomically, `ArtifactService` records it, and `CompletionService` enforces physical validity before content-quality evaluation. Model failures become failed subtask outcomes so successful parallel/tool outputs remain persistable.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, LangGraph, pytest, React 19, TypeScript, Ant Design, Vitest.

**Execution constraint:** Repository instructions prohibit autonomous Git commits. Every task ends with a diff-review checkpoint instead of a commit.

---

## File map

- Contract: `app/core/models.py`, `app/services/task_contract_service.py`, `app/services/task_service.py`
- UI: `frontend/src/api/taskhub.ts`, `frontend/src/taskConfirmation.ts`, `frontend/src/TaskConfirmationModal.tsx`, `frontend/src/taskDetailView.ts`
- Materialization: `app/core/config.py`, new `app/services/deliverable_materializer.py`
- Artifact/completion: `app/services/artifact_service.py`, `app/services/completion_service.py`
- Model/planners: `app/core/model_client.py`, `app/planners/llm_planner.py`, `app/planners/crewai_planner.py`
- Workflow persistence: `app/workflows/task_graph.py`, `app/workflows/template_runner.py`
- Documentation: `.env.example`, `README.md`
- Tests: matching pytest modules plus `frontend/src/taskConfirmation.test.ts` and `frontend/src/taskDetailView.test.ts`

---

### Task 1: Add explicit file-delivery contract fields

**Files:**
- Modify: `app/core/models.py`
- Modify: `app/services/task_contract_service.py`
- Modify: `app/services/task_service.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_database_storage.py`

- [ ] **Step 1: Write failing API, validation, and persistence tests**

Add a confirmation case equivalent to:

```python
confirmed = client.post(
    f"/api/v1/tasks/{task_id}/confirm",
    json={
        "title": "产品分析报告",
        "description": "生成产品分析报告",
        "contract": {
            "goal": "完成产品分析",
            "deliverable_goal": "一份 Markdown 报告",
            "deliverable_kind": "file",
            "deliverable_format": "markdown",
            "deliverable_filename": "product-analysis.md",
            "success_criteria": [{"id": "", "description": "报告可评审"}],
        },
    },
).json()
assert confirmed["contract"]["deliverable_kind"] == "file"
assert confirmed["contract"]["deliverable_format"] == "markdown"
assert confirmed["contract"]["deliverable_filename"] == "product-analysis.md"
```

Add parameterized cases proving file delivery rejects missing format, `/`, `\\`, `..`, NUL, and mismatched extensions; allow an empty filename or a filename without extension. Add a database round-trip assertion for all three fields.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_tasks.py tests/test_database_storage.py -k 'file_delivery or deliverable_kind'
```

Expected: tests fail because the strict contract rejects or drops the new fields.

- [ ] **Step 3: Implement the minimal model and propagation**

Add to `TaskContractInput` and `TaskDraft`:

```python
deliverable_kind: Literal["text", "file"] = "text"
deliverable_format: Literal["markdown", "text"] | None = None
deliverable_filename: str = ""
```

Extend the contract model validator:

```python
filename = self.deliverable_filename.strip()
self.deliverable_filename = filename
if self.deliverable_kind == "text":
    if self.deliverable_format is not None or filename:
        raise ValueError("text delivery cannot define file format or filename")
elif self.deliverable_format is None:
    raise ValueError("file delivery requires deliverable_format")
else:
    if filename in {".", ".."} or "/" in filename or "\\" in filename or "\x00" in filename:
        raise ValueError("deliverable_filename must be a plain filename")
    expected = ".md" if self.deliverable_format == "markdown" else ".txt"
    suffix = PurePath(filename).suffix.lower() if filename else ""
    if suffix and suffix != expected:
        raise ValueError("deliverable_filename extension does not match deliverable_format")
```

Copy the fields in `TaskContractService.confirm_contract()`. Keep legacy contracts as text through defaults. In `_merge_drafts()`, retain the first explicit file suggestion's format/name when any draft suggests file; the confirmation page remains authoritative.

- [ ] **Step 4: Verify GREEN and review**

```bash
.venv/bin/python -m pytest -q tests/test_tasks.py tests/test_database_storage.py
git diff --check -- app/core/models.py app/services/task_contract_service.py app/services/task_service.py tests/test_tasks.py tests/test_database_storage.py
```

Expected: both test modules pass and diff check exits 0. Do not stage or commit.

---

### Task 2: Add confirmation and detail UI controls

**Files:**
- Modify: `frontend/src/api/taskhub.ts`
- Modify: `frontend/src/taskConfirmation.ts`
- Modify: `frontend/src/TaskConfirmationModal.tsx`
- Modify: `frontend/src/taskDetailView.ts`
- Test: `frontend/src/taskConfirmation.test.ts`
- Test: `frontend/src/taskDetailView.test.ts`

- [ ] **Step 1: Write failing draft, validation, payload, and detail tests**

Extend draft expectations with:

```ts
deliverableKind: "file",
deliverableFormat: "markdown",
deliverableFilename: "product-analysis.md",
```

Assert the payload includes the corresponding snake-case fields. Add validation cases for missing format, path separators, and mismatched extension. Add a detail test expecting `一份可评审报告（文件 / Markdown / product-analysis.md）`.

- [ ] **Step 2: Run Vitest and verify RED**

```bash
npm --prefix frontend test -- taskConfirmation.test.ts taskDetailView.test.ts
```

Expected: TypeScript/test failures because the new fields do not exist.

- [ ] **Step 3: Implement frontend types and pure form logic**

Add:

```ts
export type DeliverableKind = "text" | "file"
export type DeliverableFormat = "markdown" | "text"
```

Add snake-case fields to `TaskContractInput`, `TaskContract`, and `Task.draft`. Add camel-case fields to `ConfirmationDraft`:

```ts
deliverableKind: DeliverableKind
deliverableFormat: DeliverableFormat | null
deliverableFilename: string
```

Accept only exact model suggestions, default old tasks to text, mirror backend validation, and send cleared file fields for text delivery.

- [ ] **Step 4: Implement visible controls and detail display**

Import Ant Design `Select`. Add delivery kind after “交付物目标”; show format and optional filename only for file delivery. Switching to text clears format/name. Use a pure helper for detail text:

```ts
if (contract?.deliverable_kind !== "file") return goal
const format = contract.deliverable_format === "text" ? "纯文本" : "Markdown"
const filename = cleanText(contract.deliverable_filename)
return `${goal}（文件 / ${format}${filename ? ` / ${filename}` : ""}）`
```

- [ ] **Step 5: Verify GREEN, build, and review**

```bash
npm --prefix frontend test -- taskConfirmation.test.ts taskDetailView.test.ts
npm --prefix frontend run build
git diff --check -- frontend/src/api/taskhub.ts frontend/src/taskConfirmation.ts frontend/src/TaskConfirmationModal.tsx frontend/src/taskDetailView.ts frontend/src/taskConfirmation.test.ts frontend/src/taskDetailView.test.ts
```

Expected: focused tests pass, build exits 0, and diff check exits 0. Do not stage or commit.

---

### Task 3: Implement safe atomic document materialization

**Files:**
- Modify: `app/core/config.py`
- Create: `app/services/deliverable_materializer.py`
- Create: `tests/test_deliverable_materializer.py`

- [ ] **Step 1: Write failing real-filesystem tests**

```python
def test_materializer_writes_markdown_under_execution_directory(tmp_path: Path) -> None:
    task = file_delivery_task(filename="pdd-report")
    result = DeliverableMaterializer(tmp_path).materialize(task, "# 拼多多产品分析\n\n正文")
    assert result.path == tmp_path / task.id / task.active_execution_id / "pdd-report.md"
    assert result.path.read_text(encoding="utf-8") == "# 拼多多产品分析\n\n正文"
    assert result.media_type == "text/markdown"
```

Add TXT, default filename, empty content, missing execution, unsafe ID, wrong extension, and missing parent directory cases. Add a rerun-style case that changes `active_execution_id`, writes again, and proves the second execution gets a different directory without modifying the first file.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_deliverable_materializer.py
```

Expected: import/collection failure because the service does not exist.

- [ ] **Step 3: Add canonical output configuration**

```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "agent_outputs"

def get_agent_output_dir() -> Path:
    configured = os.getenv("AGENT_OUTPUT_DIR", "").strip()
    if not configured:
        return DEFAULT_AGENT_OUTPUT_DIR
    path = Path(configured).expanduser()
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
```

- [ ] **Step 4: Implement the focused service**

```python
@dataclass(frozen=True)
class MaterializedDeliverable:
    path: Path
    content: str
    media_type: str
    delivery_format: Literal["markdown", "text"]

class DeliverableMaterializer:
    def __init__(self, output_root: Path | None = None) -> None:
        self.output_root = (output_root or get_agent_output_dir()).expanduser().resolve()

    def materialize(self, task: Task, content: str) -> MaterializedDeliverable:
        normalized = content.strip()
        if not normalized:
            raise ValueError("file deliverable content is empty")
        contract = task.contract
        if contract is None or contract.deliverable_kind != "file" or contract.deliverable_format is None:
            raise ValueError("task contract does not require a file deliverable")
        task_id = self._safe_segment(task.id, "task id")
        execution_id = self._safe_segment(task.active_execution_id or "", "execution id")
        extension = ".md" if contract.deliverable_format == "markdown" else ".txt"
        filename = self._filename(contract.deliverable_filename, task_id, extension)
        directory = (self.output_root / task_id / execution_id).resolve()
        if self.output_root != directory and self.output_root not in directory.parents:
            raise ValueError("deliverable output path escapes AGENT_OUTPUT_DIR")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        temporary = directory / f".{filename}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(normalized, encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return MaterializedDeliverable(target, normalized, self._media_type(extension), contract.deliverable_format)
```

Implement `_safe_segment()`, `_filename()`, and `_media_type()` with the tested rules.

- [ ] **Step 5: Verify GREEN and review**

```bash
.venv/bin/python -m pytest -q tests/test_deliverable_materializer.py
git diff --check -- app/core/config.py app/services/deliverable_materializer.py tests/test_deliverable_materializer.py
```

Expected: all materializer tests pass and diff check exits 0. Do not stage or commit.

---

### Task 4: Register final FILE Artifacts and enforce the physical gate

**Files:**
- Modify: `app/services/artifact_service.py`
- Modify: `app/services/completion_service.py`
- Modify: `app/services/task_service.py`
- Test: `tests/test_artifacts.py`
- Test: `tests/test_completion_service.py`

- [ ] **Step 1: Write failing registration and revalidation tests**

```python
materialized = DeliverableMaterializer(tmp_path).materialize(task, "# Report")
artifact = ArtifactService().register_task_file_output(task, materialized)
assert artifact.kind == ArtifactKind.FILE
assert artifact.source_type == ArtifactSourceType.TASK_RESULT
assert artifact.content == "# Report"
assert artifact.uri == materialized.path.as_uri()
assert artifact.metadata["deliverable_format"] == "markdown"
assert artifact.metadata["content_length"] == len("# Report")
assert artifact.validation_status == ArtifactValidationStatus.VALID
```

Add revalidation cases for deleted, modified, zero-byte, and content-snapshot-mismatched files.

- [ ] **Step 2: Write failing completion tests**

Inject `DeliverableMaterializer(tmp_path)` into `CompletionService`. Assert successful candidate materializes `task.context.summary`, registers a FILE Artifact, and returns success only after checksum validation. Also assert:

- a generic TEXT Artifact cannot satisfy a file contract;
- a materializer stub raising `OSError("write denied")` blocks with an explicit sanitized file-delivery gap;
- failed/blocked/cancelled candidates do not write an official final file;
- requirement evaluation receives only the managed final FILE Artifact for file delivery.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_artifacts.py tests/test_completion_service.py -k 'file_output or file_delivery or materializ'
```

Expected: failures because registration, injection, materialization, and the gate do not exist.

- [ ] **Step 4: Add final FILE Artifact registration**

Implement `ArtifactService.register_task_file_output()`:

```python
return self._register(
    task,
    kind=ArtifactKind.FILE,
    source_type=ArtifactSourceType.TASK_RESULT,
    source_id=f"{task.id}:file",
    name=materialized.path.name,
    content=materialized.content,
    uri=materialized.path.as_uri(),
    media_type=materialized.media_type,
    checksum=self._file_checksum(materialized.path),
    validation_status=ArtifactValidationStatus.VALID,
    validation_reason="Managed file exists and content checksum matches",
    metadata={
        "managed_final_delivery": True,
        "deliverable_format": materialized.delivery_format,
        "content_length": len(materialized.content),
    },
)
```

Extend file revalidation so empty files and a body snapshot whose UTF-8 checksum differs from the physical file become invalid.

- [ ] **Step 5: Materialize only candidate success and add deterministic gaps**

Inject a shared materializer from `TaskService`. Add:

```python
def delivery_content(self, task: Task, output: str) -> str:
    if task.contract and task.contract.deliverable_kind == "file":
        return task.context.summary.strip() or output.strip()
    return output.strip()
```

In `finalize()`:

1. Materialize and register a file only when `candidate_status == SUCCEEDED` and the contract requires file delivery.
2. Convert `ValueError`/`OSError` into a sanitized completion gap.
3. Retain text registration for text delivery.
4. Require a valid managed FILE Artifact inside `materializer.output_root` with expected extension, MIME type, non-empty content, and matching checksum/body snapshot.
5. For file delivery, pass only managed valid FILE Artifacts to `_evaluate_deliverables()`.

Do not mark content requirements passed merely because the physical file exists.

- [ ] **Step 6: Verify GREEN and review**

```bash
.venv/bin/python -m pytest -q tests/test_artifacts.py tests/test_completion_service.py
git diff --check -- app/services/artifact_service.py app/services/completion_service.py app/services/task_service.py tests/test_artifacts.py tests/test_completion_service.py
```

Expected: both modules pass and diff check exits 0. Do not stage or commit.

---

### Task 5: Pass the contract to models and return final documents as plain bodies

**Files:**
- Modify: `app/core/model_client.py`
- Modify: `app/planners/llm_planner.py`
- Modify: `app/planners/crewai_planner.py`
- Test: `tests/test_model_client.py`
- Test: `tests/test_planners.py`

- [ ] **Step 1: Write failing intent and planner payload tests**

Extend an intent result with:

```python
"deliverable_kind": "file",
"deliverable_format": "markdown",
"deliverable_filename": "implementation-plan.md",
```

Assert the fields reach `TaskDraft`. Capture both planner inputs and assert:

```python
assert payload["task"]["contract"] == task.contract.model_dump(mode="json")
```

- [ ] **Step 2: Write failing file-output protocol tests**

For a file contract whose Agent only has `file_write`, return multiline Markdown and assert it becomes `( [], complete_body )`, the model-visible tool list omits `file_write`, and the prompt says to return the complete body directly. For an Agent with a lookup tool plus `file_write`, assert a small lookup JSON request still works and the follow-up Markdown is final output. Add a case where a hallucinated managed `file_write` request is rejected and retried without execution.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_model_client.py tests/test_planners.py -k 'file_delivery or contract_payload or suggested_contract_fields'
```

Expected: failures because the contract/file protocol is absent.

- [ ] **Step 4: Add intent fields and complete contract payloads**

Add the three fields to the intent JSON schema and accept only exact values. Include:

```python
"contract": task.contract.model_dump(mode="json") if task.contract else None
```

in the LLM planner, CrewAI planner, legacy planner helper, and subtask execution `main_task` payload.

- [ ] **Step 5: Implement managed file-delivery execution behavior**

Add:

```python
def _is_managed_file_delivery(task: Task) -> bool:
    return bool(task.contract and task.contract.deliverable_kind == "file")

def _execution_tools(agent: Agent | None, managed_file: bool) -> list[AgentTool]:
    tools = list(agent.tools) if agent else []
    return [tool for tool in tools if not (managed_file and tool.type == "file_write")]
```

For managed file delivery, omit `file_write` from the model-visible payload and instruct: auxiliary tool requests use short JSON; the final Markdown/TXT body is returned directly. If no visible tools exist, treat the whole non-empty model response as final output without JSON parsing. If tools exist, retain JSON-object-or-plain-text parsing. Reject any returned call resolving to Agent tool type `file_write` so the existing retry loop can request the correct protocol.

Keep text-delivery behavior unchanged.

- [ ] **Step 6: Verify GREEN and review**

```bash
.venv/bin/python -m pytest -q tests/test_model_client.py tests/test_planners.py
git diff --check -- app/core/model_client.py app/planners/llm_planner.py app/planners/crewai_planner.py tests/test_model_client.py tests/test_planners.py
```

Expected: both modules pass and diff check exits 0. Do not stage or commit.

---

### Task 6: Preserve completed outputs when model/parallel work fails

**Files:**
- Modify: `app/workflows/task_graph.py`
- Modify: `app/workflows/template_runner.py`
- Test: `tests/test_task_graph.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing outcome and persistence tests**

Replace fallback-disabled exception expectations with:

```python
outcome = runner._execute_subtask(task, subtask, agent)
assert outcome.completed is False
assert outcome.error.startswith("Agent model execution failed")
assert sensitive_value not in outcome.error
```

Add an integration case where successful `file_write` is followed by `AgentModelExecutionError`; after `runner.run(task)`, assert the failed task retains its round, tool result, physical file, and FILE Artifact.

- [ ] **Step 2: Add failing parallel/store tests**

Create two parallel subtasks: one succeeds and one raises `AgentModelExecutionError`. Assert both are recorded in plan order and the successful Artifact remains. Add a `TaskService.run_confirmed_task()` case asserting the persisted store contains those rounds/Artifacts rather than only a top-level error Artifact.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_task_graph.py tests/test_tasks.py tests/test_workflows.py -k 'model_execution_error or followup_error or parallel_model_failure or preserves_working'
```

Expected: old tests expect `RuntimeError`; new state assertions fail because the working task is discarded.

- [ ] **Step 4: Convert Agent model errors to failed outcomes**

```python
if is_system_mock_fallback_enabled():
    return SubTaskExecutionOutcome(completed=True, output=mock_agent_execution(task, agent))
return SubTaskExecutionOutcome(
    completed=False,
    error=(
        f"Agent model execution failed during {phase} "
        f"after {error.attempts} attempts: {error.last_error}"
    ),
)
```

The existing application phase must register successful tool results before marking the subtask failed, append the round, finalize failure, and return normally so `TaskService` saves the full working task.

- [ ] **Step 5: Use merged delivery content in both workflow types**

Normalize the proposed completion output through `completion_service.delivery_content(task, proposed_output)` before criteria evaluation and finalization in `TaskGraphRunner._completion_judge()` and `WorkflowTemplateRunner._complete_workflow()`.

- [ ] **Step 6: Verify GREEN and review**

```bash
.venv/bin/python -m pytest -q tests/test_task_graph.py tests/test_tasks.py tests/test_workflows.py
git diff --check -- app/workflows/task_graph.py app/workflows/template_runner.py tests/test_task_graph.py tests/test_tasks.py tests/test_workflows.py
```

Expected: all three modules pass and diff check exits 0. Do not stage or commit.

---

### Task 7: Document configuration and run complete automated verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Verify: all changed files

- [ ] **Step 1: Document non-secret configuration**

```dotenv
AGENT_OUTPUT_DIR=./runtime/agent_outputs
```

State that relative paths resolve from the project root, outputs use task/execution subdirectories, supported formats are Markdown/TXT, and file delivery cannot succeed without a valid current FILE Artifact.

- [ ] **Step 2: Run complete backend verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile app/core/models.py app/core/config.py app/core/model_client.py app/services/deliverable_materializer.py app/services/artifact_service.py app/services/completion_service.py app/services/task_service.py app/workflows/task_graph.py app/workflows/template_runner.py
```

Expected: zero failures and both commands exit 0; unchanged third-party deprecation warnings may remain.

- [ ] **Step 3: Run complete frontend verification**

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: all Vitest tests pass and production build exits 0.

- [ ] **Step 4: Review the complete diff without committing**

```bash
git diff --check
git status --short
git diff --stat
```

Expected: diff check exits 0; status contains only intended work plus pre-existing user changes. Do not stage or commit.

---

### Task 8: Perform real page-to-file acceptance with the configured model

**Files:**
- Runtime output only: `runtime/agent_outputs/<task_id>/<execution_id>/`
- No source edits unless verification exposes a reproducible defect; then return to RED with one focused test.

- [ ] **Step 1: Restart services without exposing credentials**

Use the existing environment and verify these non-secret settings without printing the model key:

```text
ENABLE_SYSTEM_MOCK_FALLBACK=false
AGENT_OUTPUT_DIR=<project-root>/runtime/agent_outputs
MODEL_MAX_OUTPUT_TOKENS=1024000
```

- [ ] **Step 2: Create and confirm from the page**

Create `拼多多产品分析报告`; choose `文件`, `Markdown`, filename `pinduoduo-product-analysis.md`; confirm the product-positioning, target-user, core-function, competitor, opportunity/risk, and conclusion requirements; start asynchronous execution.

- [ ] **Step 3: Follow execution to terminal**

Poll visible execution history until terminal. Acceptance requires `succeeded`; `failed`, `blocked`, timeout, mock output, or parser error fails acceptance.

- [ ] **Step 4: Verify Artifact and physical file**

```bash
find runtime/agent_outputs/<task_id>/<execution_id> -maxdepth 1 -type f -name 'pinduoduo-product-analysis.md' -size +0c -print
shasum -a 256 runtime/agent_outputs/<task_id>/<execution_id>/pinduoduo-product-analysis.md
```

Compare the digest to the Artifact checksum after removing its `sha256:` prefix. Read the file and confirm every selected report section is present.

- [ ] **Step 5: Record evidence**

Report task ID, execution ID, terminal status, relative file path, byte size, checksum match, Artifact ID, and automated test totals. Do not include credentials, request headers, or private model endpoints. Keep services running unless asked to stop.

---

## Completion checklist

- [ ] Confirmation explicitly distinguishes text/file and Markdown/TXT.
- [ ] Long final bodies bypass JSON tool arguments.
- [ ] Final file writes are atomic and constrained to the canonical root.
- [ ] FILE Artifact path, body snapshot, size, MIME type, and checksum are validated.
- [ ] TEXT Artifact cannot satisfy a file contract.
- [ ] Failed model/parallel execution preserves successful outputs.
- [ ] Reruns write to independent execution directories.
- [ ] Backend tests, frontend tests, and frontend build pass.
- [ ] Real browser-created Pinduoduo task succeeds with a verified file.
- [ ] No Git commit is created.
