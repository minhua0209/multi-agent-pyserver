from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.core.models as core_models
import app.services.execution_service as execution_module
import app.services.task_service as task_service_module
from app.core.enums import CurrentNode, ExecutionTriggerType, SourceType, TaskStatus, TaskType
from app.core.models import (
    Artifact,
    CompletionReport,
    RoundPlan,
    SubTask,
    Task,
    TaskContext,
    TaskContract,
    TaskContractItem,
    TaskDraft,
    TaskExecution,
    TaskRound,
    ToolExecutionResult,
    User,
    WorkflowNode,
    scoped_subtask_id,
    utc_now,
)
from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
)
from app.main import create_app
from app.services.execution_service import ExecutionService
from app.services.storage import AgentRegistry, InMemoryTaskStore
from app.services.task_service import TaskService
from app.workflows.task_graph import TaskGraphRunner
from app.workflows.template_runner import WorkflowTemplateRunner


def _contract() -> TaskContract:
    return TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[TaskContractItem(id="criterion_1", description="Reviewable")],
        confirmed_at=utc_now(),
    )


def _execution(execution_id: str, attempt_no: int) -> TaskExecution:
    now = utc_now()
    return TaskExecution(
        id=execution_id,
        task_id="task_1",
        attempt_no=attempt_no,
        trigger_type=(
            ExecutionTriggerType.INITIAL
            if attempt_no == 1
            else ExecutionTriggerType.RERUN
        ),
        contract_snapshot=_contract(),
        status=TaskStatus.SUCCEEDED,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.COMPLETION_JUDGE,
        created_at=now,
        finished_at=now,
    )


def _task_data(executions: list[dict], active_execution_id: str) -> dict:
    now = utc_now()
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        task_status=TaskStatus.SUCCEEDED,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=_contract(),
        executions=[_execution("execution_base", 1)],
        active_execution_id="execution_base",
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json") | {
        "executions": executions,
        "active_execution_id": active_execution_id,
    }


def test_rerun_request_models_validate_required_fields_and_execution_mode() -> None:
    preflight_class = getattr(core_models, "TaskRerunPreflightRequest", None)
    create_class = getattr(core_models, "TaskRerunCreate", None)

    assert preflight_class is not None
    assert create_class is not None
    assert preflight_class(source_execution_id=" execution_1 ").source_execution_id == "execution_1"
    payload = create_class(
        source_execution_id=" execution_1 ",
        reason=" retry after review ",
        execution_mode="async",
    )
    assert payload.source_execution_id == "execution_1"
    assert payload.reason == "retry after review"
    assert payload.execution_mode == "async"

    with pytest.raises(ValidationError):
        preflight_class(source_execution_id="   ")
    with pytest.raises(ValidationError):
        create_class(source_execution_id="execution_1", reason="   ")
    with pytest.raises(ValidationError):
        create_class(
            source_execution_id="execution_1",
            reason="retry",
            execution_mode="parallel",
        )


def test_task_rejects_duplicate_non_empty_execution_idempotency_keys() -> None:
    first = _execution("execution_1", 1).model_dump(mode="json")
    second = _execution("execution_2", 2).model_dump(mode="json")
    first["idempotency_key"] = "rerun-key"
    second["idempotency_key"] = "rerun-key"
    second["retry_of_execution_id"] = "execution_1"

    with pytest.raises(ValidationError, match="idempotency"):
        Task.model_validate(_task_data([first, second], "execution_2"))


def test_task_rejects_retry_reference_to_same_or_newer_execution() -> None:
    first = _execution("execution_1", 1).model_dump(mode="json")
    second = _execution("execution_2", 2).model_dump(mode="json")
    first["retry_of_execution_id"] = "execution_2"

    with pytest.raises(ValidationError, match="retry_of_execution_id"):
        Task.model_validate(_task_data([first, second], "execution_2"))


def _actor() -> User:
    now = utc_now()
    return User(id="user_1", name="Rerun Owner", created_at=now, updated_at=now)


def _finished_task(
    status: TaskStatus = TaskStatus.SUCCEEDED,
    *,
    tool_results: list[ToolExecutionResult] | None = None,
) -> Task:
    now = utc_now()
    initial_context = TaskContext(summary="initial input", artifacts=["brief.txt"])
    source_context = TaskContext(
        summary="completed output",
        rounds=[
            TaskRound(
                round_index=1,
                subtasks=[
                    SubTask(
                        id="task_1_execution_1_step",
                        execution_id="execution_1",
                        logical_key="step",
                        title="Step",
                        description="Run step",
                        status=TaskStatus.SUCCEEDED,
                        tool_results=tool_results or [],
                        output="done",
                    )
                ],
            )
        ],
    )
    source = TaskExecution(
        id="execution_1",
        task_id="task_1",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=_contract(),
        workflow_snapshot={"nodes": [{"id": "start"}], "edges": []},
        status=status,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.COMPLETION_JUDGE,
        context_snapshot=source_context.model_copy(deep=True),
        loop_count=1,
        final_output="completed output",
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        created_by_user_id="user_1",
        created_by_user_name="Rerun Owner",
        task_status=status,
        current_node=CurrentNode.COMPLETION_JUDGE,
        contract=_contract(),
        context=source_context,
        initial_context=initial_context,
        executions=[source],
        active_execution_id=source.id,
        assigned_agent_id="agent_1",
        final_output="completed output",
        loop_count=1,
        created_at=now,
        updated_at=now,
    )


def _rename_task_id(task: Task, task_id: str) -> None:
    task.id = task_id
    for execution in task.executions:
        execution.task_id = task_id


def _rerun_payload(
    *,
    reason: str = "Retry after review",
    execution_mode: str = "sync",
    confirm_side_effects: bool = False,
):
    return core_models.TaskRerunCreate(
        source_execution_id="execution_1",
        reason=reason,
        execution_mode=execution_mode,
        confirm_side_effects=confirm_side_effects,
    )


def test_execution_service_lists_gets_and_rejects_missing_execution() -> None:
    task = _finished_task()
    missing_error = getattr(execution_module, "ExecutionNotFoundError", None)
    assert missing_error is not None
    service = ExecutionService()

    assert service.list(task) == task.executions
    assert service.get(task, "execution_1") == task.executions[0]
    with pytest.raises(missing_error):
        service.get(task, "execution_missing")


@pytest.mark.parametrize("status", list(TaskStatus)[1:])
def test_create_rerun_from_every_terminal_status_resets_projection_and_preserves_source(
    status: TaskStatus,
) -> None:
    task = _finished_task(status)
    source_before = task.executions[0].model_dump_json()

    execution = ExecutionService().create_rerun(
        task,
        _rerun_payload(),
        _actor(),
        idempotency_key="rerun-key-1",
        request_fingerprint="fingerprint-1",
        start_node=CurrentNode.DISPATCH_DECISION,
    )

    assert task.executions[0].model_dump_json() == source_before
    assert execution.attempt_no == 2
    assert execution.trigger_type == ExecutionTriggerType.RERUN
    assert execution.retry_of_execution_id == "execution_1"
    assert execution.idempotency_key == "rerun-key-1"
    assert execution.request_fingerprint == "fingerprint-1"
    assert task.active_execution_id == execution.id
    assert task.contract == task.executions[0].contract_snapshot
    assert task.task_status == TaskStatus.RUNNING
    assert task.current_node == CurrentNode.DISPATCH_DECISION
    assert task.context == task.initial_context
    assert task.artifacts == []
    assert task.loop_count == 0
    assert task.final_output == ""
    assert task.completion_report is None
    assert task.assigned_agent_id is None


@pytest.mark.parametrize("case", ["unconfirmed", "source_running", "current_running"])
def test_rerun_preflight_rejects_ineligible_task_without_mutation(case: str) -> None:
    if case == "unconfirmed":
        now = utc_now()
        task = Task(
            id="task_1",
            source_type=SourceType.BUSINESS_SYSTEM,
            content="Cancelled before confirmation",
            task_status=TaskStatus.CANCELLED,
            current_node=CurrentNode.COMPLETION_JUDGE,
            created_at=now,
            updated_at=now,
        )
    else:
        task = _finished_task()
        if case == "source_running":
            task.executions[0].status = TaskStatus.RUNNING
            task.executions[0].finished_at = None
            task.task_status = TaskStatus.RUNNING
        else:
            running = _execution("execution_2", 2).model_copy(
                update={
                    "status": TaskStatus.RUNNING,
                    "finished_at": None,
                    "retry_of_execution_id": "execution_1",
                }
            )
            task.executions.append(running)
            task.active_execution_id = running.id
            task.task_status = TaskStatus.RUNNING
    before = task.model_dump_json()

    response = ExecutionService().preflight(
        task,
        core_models.TaskRerunPreflightRequest(source_execution_id="execution_1"),
    )

    assert response.allowed is False
    assert response.issues
    assert task.model_dump_json() == before


@pytest.mark.parametrize(
    "case",
    [
        "task_not_terminal",
        "active_missing",
        "status_mismatch",
        "current_node_mismatch",
        "context_mismatch",
        "artifacts_mismatch",
        "output_mismatch",
        "report_mismatch",
        "running_finished",
        "terminal_unfinished",
    ],
)
def test_preflight_rejects_inconsistent_execution_projection_without_mutation(
    case: str,
) -> None:
    task = _finished_task()
    active = task.executions[0]
    if case == "task_not_terminal":
        task.task_status = TaskStatus.RUNNING
    elif case == "active_missing":
        task.active_execution_id = "execution_missing"
    elif case == "status_mismatch":
        active.status = TaskStatus.FAILED
    elif case == "current_node_mismatch":
        active.current_node = CurrentNode.HUMAN_INTERVENTION
    elif case == "context_mismatch":
        active.context_snapshot.summary = "different context"
    elif case == "artifacts_mismatch":
        active.artifacts.append(
            Artifact(
                id="artifact_execution_only",
                task_id=task.id,
                execution_id=active.id,
                kind=ArtifactKind.TEXT,
                source_type=ArtifactSourceType.TASK_RESULT,
                source_id="execution_only",
                name="Execution only",
                content="not projected",
                validation_status=ArtifactValidationStatus.VALID,
                created_at=utc_now(),
            )
        )
    elif case == "output_mismatch":
        active.final_output = "different output"
    elif case == "report_mismatch":
        task.completion_report = CompletionReport(
            id="completion_mismatch",
            execution_id=active.id,
            terminal_status=task.task_status,
            completion_reason="Top-level only report",
            decided_at=utc_now(),
        )
    elif case == "running_finished":
        malformed = _execution("execution_malformed", 2).model_copy(
            update={
                "status": TaskStatus.RUNNING,
                "finished_at": utc_now(),
                "retry_of_execution_id": "execution_1",
            }
        )
        task.executions.append(malformed)
    elif case == "terminal_unfinished":
        malformed = _execution("execution_malformed", 2).model_copy(
            update={
                "status": TaskStatus.FAILED,
                "finished_at": None,
                "retry_of_execution_id": "execution_1",
            }
        )
        task.executions.append(malformed)
    before = task.model_dump_json()
    service = ExecutionService()

    preflight = service.preflight(
        task,
        core_models.TaskRerunPreflightRequest(source_execution_id="execution_1"),
        dependencies_satisfied=True,
    )

    assert preflight.allowed is False
    assert preflight.issues
    not_allowed = getattr(execution_module, "TaskRerunNotAllowedError")
    with pytest.raises(not_allowed):
        service.create_rerun(
            task,
            _rerun_payload(),
            _actor(),
            idempotency_key="inconsistent-key",
            request_fingerprint="inconsistent-fingerprint",
            start_node=CurrentNode.DISPATCH_DECISION,
            dependencies_satisfied=True,
        )
    assert task.model_dump_json() == before


def test_preflight_rejects_only_active_loop_count_mismatch_without_mutation() -> None:
    task = _finished_task()
    task.executions[0].loop_count = task.loop_count + 1
    before = task.model_dump_json()
    service = ExecutionService()

    preflight = service.preflight(
        task,
        core_models.TaskRerunPreflightRequest(source_execution_id="execution_1"),
        dependencies_satisfied=True,
    )

    assert preflight.allowed is False
    assert [issue.code for issue in preflight.issues] == [
        "active_loop_count_mismatch"
    ]
    not_allowed = getattr(execution_module, "TaskRerunNotAllowedError")
    with pytest.raises(not_allowed):
        service.create_rerun(
            task,
            _rerun_payload(),
            _actor(),
            idempotency_key="loop-count-mismatch-key",
            request_fingerprint="loop-count-mismatch-fingerprint",
            start_node=CurrentNode.DISPATCH_DECISION,
            dependencies_satisfied=True,
        )
    assert task.model_dump_json() == before


def test_rerun_side_effects_require_confirmation_and_record_actor() -> None:
    tool_results = [
        ToolExecutionResult(
            tool_execution_id="tool_smtp",
            tool_name="send_email",
            tool_type="smtp_email",
            side_effect=True,
            side_effect_known=True,
            success=False,
            error="timeout",
        ),
        ToolExecutionResult(
            tool_execution_id="tool_file",
            tool_name="write_file",
            tool_type="file_write",
            side_effect=True,
            side_effect_known=True,
            success=True,
            result="/tmp/report.txt",
        ),
        ToolExecutionResult(
            tool_execution_id="tool_http_post",
            tool_name="create_record",
            tool_type="http",
            side_effect=True,
            side_effect_known=True,
            success=True,
            result="created",
        ),
        ToolExecutionResult(
            tool_execution_id="tool_http_old",
            tool_name="legacy_http",
            tool_type="http",
            success=True,
            result="legacy",
        ),
        ToolExecutionResult(
            tool_execution_id="tool_http_get",
            tool_name="read_record",
            tool_type="http",
            side_effect=False,
            side_effect_known=True,
            success=True,
            result="read",
        ),
    ]
    task = _finished_task(tool_results=tool_results)
    service = ExecutionService()
    preflight = service.preflight(
        task,
        core_models.TaskRerunPreflightRequest(source_execution_id="execution_1"),
    )

    assert preflight.allowed is True
    assert preflight.requires_side_effect_confirmation is True
    assert {item.tool_execution_id for item in preflight.side_effects} == {
        "tool_smtp",
        "tool_file",
        "tool_http_post",
        "tool_http_old",
    }
    assert all(not hasattr(item, "arguments") for item in preflight.side_effects)
    confirmation_error = getattr(
        execution_module,
        "TaskRerunSideEffectConfirmationRequiredError",
        None,
    )
    assert confirmation_error is not None
    before = task.model_dump_json()
    with pytest.raises(confirmation_error):
        service.create_rerun(
            task,
            _rerun_payload(),
            _actor(),
            idempotency_key="rerun-key-side-effect",
            request_fingerprint="fingerprint-side-effect",
            start_node=CurrentNode.DISPATCH_DECISION,
        )
    assert task.model_dump_json() == before

    execution = service.create_rerun(
        task,
        _rerun_payload(confirm_side_effects=True),
        _actor(),
        idempotency_key="rerun-key-side-effect",
        request_fingerprint="fingerprint-side-effect-confirmed",
        start_node=CurrentNode.DISPATCH_DECISION,
    )

    assert execution.side_effects_confirmed_by_user_id == "user_1"
    assert execution.side_effects_confirmed_by_user_name == "Rerun Owner"
    assert execution.side_effects_confirmed_at is not None


def test_task_service_idempotency_replays_before_running_preflight_and_conflicts_on_payload(
    tmp_path,
) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    payload = _rerun_payload(execution_mode="async")

    first = service.create_rerun("task_1", payload, _actor(), "rerun-key")
    replayed = service.create_rerun("task_1", payload, _actor(), "rerun-key")

    assert first.replayed is False
    assert replayed.replayed is True
    assert replayed.execution.id == first.execution.id
    assert len(store.get("task_1").executions) == 2

    conflict_error = getattr(task_service_module, "TaskRerunIdempotencyConflictError", None)
    assert conflict_error is not None
    with pytest.raises(conflict_error):
        service.create_rerun(
            "task_1",
            _rerun_payload(reason="Different payload", execution_mode="async"),
            _actor(),
            "rerun-key",
        )


def test_task_service_concurrent_same_idempotency_key_creates_one_execution(tmp_path) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    payload = _rerun_payload(execution_mode="async")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: service.create_rerun(
                    "task_1",
                    payload,
                    _actor(),
                    "concurrent-key",
                ),
                range(2),
            )
        )

    assert {response.execution.id for response in responses} == {
        responses[0].execution.id
    }
    assert sorted(response.replayed for response in responses) == [False, True]
    assert len(store.get("task_1").executions) == 2


def test_late_worker_with_stale_expected_execution_id_is_a_no_op(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    rerun = service.create_rerun(
        "task_1",
        _rerun_payload(execution_mode="async"),
        _actor(),
        "late-worker-key",
    )
    before = rerun.task.model_dump_json()
    monkeypatch.setattr(
        service,
        "_run_automatic_flow",
        lambda _task: (_ for _ in ()).throw(AssertionError("stale worker must not run")),
    )

    returned = service.run_confirmed_task(
        "task_1",
        expected_execution_id="execution_1",
    )

    assert returned.model_dump_json() == before
    assert store.get("task_1").model_dump_json() == before


def test_stale_schedule_and_background_start_have_no_side_effects(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    response = service.create_rerun(
        "task_1",
        _rerun_payload(execution_mode="async"),
        _actor(),
        "stale-schedule-key",
    )
    before = response.task.model_dump_json()
    threads = []
    monkeypatch.setattr(
        task_service_module,
        "Thread",
        lambda *args, **kwargs: threads.append((args, kwargs)),
    )

    scheduled = service.schedule_confirmed_task(
        "task_1",
        expected_execution_id="execution_1",
    )
    service.start_background_task(
        "task_1",
        expected_execution_id="execution_1",
    )

    assert scheduled.model_dump_json() == before
    assert store.get("task_1").model_dump_json() == before
    assert threads == []


def test_finished_execution_is_not_scheduled_started_or_run(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    store = InMemoryTaskStore()
    store.save(task)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    before = task.model_dump_json()
    threads = []
    runs = []
    monkeypatch.setattr(
        task_service_module,
        "Thread",
        lambda *args, **kwargs: threads.append((args, kwargs)),
    )
    monkeypatch.setattr(
        service,
        "_run_automatic_flow",
        lambda current: runs.append(current.id),
    )

    scheduled = service.schedule_confirmed_task(task.id, task.active_execution_id)
    service.start_background_task(task.id, task.active_execution_id)
    returned = service.run_confirmed_task(task.id, task.active_execution_id)

    assert scheduled.model_dump_json() == before
    assert returned.model_dump_json() == before
    assert store.get(task.id).model_dump_json() == before
    assert threads == []
    assert runs == []


def test_execution_is_claimed_once_across_repeated_start_and_run(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    rerun = service.create_rerun(
        "task_1",
        _rerun_payload(execution_mode="async"),
        _actor(),
        "claim-once-key",
    )
    execution_id = rerun.execution.id
    threads = []
    runs = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            threads.append((args, kwargs))

        def start(self):
            return None

    monkeypatch.setattr(task_service_module, "Thread", FakeThread)
    monkeypatch.setattr(
        service,
        "_run_automatic_flow",
        lambda current: runs.append(current.id) or current,
    )

    service.start_background_task("task_1", execution_id)
    service.start_background_task("task_1", execution_id)
    service.run_confirmed_task("task_1", execution_id)

    claimed = service.get_execution("task_1", execution_id)
    assert claimed.started_at is not None
    assert len(threads) == 1
    assert runs == []


def test_direct_run_claims_execution_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    store.save(_finished_task())
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    rerun = service.create_rerun(
        "task_1",
        _rerun_payload(),
        _actor(),
        "direct-claim-key",
    )
    execution_id = rerun.execution.id
    runs = []
    monkeypatch.setattr(
        service,
        "_run_automatic_flow",
        lambda current: runs.append(current.id) or current,
    )

    service.run_confirmed_task("task_1", execution_id)
    service.run_confirmed_task("task_1", execution_id)

    assert runs == ["task_1"]
    assert service.get_execution("task_1", execution_id).started_at is not None


def test_dependency_waiting_execution_is_not_claimed_or_mutated(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    task = _finished_task()
    task.dependency_task_ids = ["dependency_missing"]
    store.save(task)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    rerun = service.create_rerun(
        task.id,
        _rerun_payload(execution_mode="async"),
        _actor(),
        "waiting-claim-key",
    )
    before = rerun.task.model_dump_json()
    threads = []
    monkeypatch.setattr(
        task_service_module,
        "Thread",
        lambda *args, **kwargs: threads.append((args, kwargs)),
    )

    service.start_background_task(task.id, rerun.execution.id)
    returned = service.run_confirmed_task(task.id, rerun.execution.id)

    assert returned.model_dump_json() == before
    assert store.get(task.id).model_dump_json() == before
    assert service.get_execution(task.id, rerun.execution.id).started_at is None
    assert threads == []


def test_resume_unblocked_task_claims_after_saving_dependency_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    dependency = _finished_task()
    dependency.id = "dependency_1"
    dependency.executions[0].task_id = dependency.id
    dependency.artifacts = []
    store.save(dependency)
    candidate = _finished_task()
    candidate.id = "candidate_1"
    candidate.title = "Candidate"
    candidate.description = "Candidate waiting for dependency"
    candidate.executions[0].task_id = candidate.id
    candidate.active_execution_id = candidate.executions[0].id
    candidate.task_status = TaskStatus.RUNNING
    candidate.current_node = CurrentNode.WAITING_DEPENDENCIES
    candidate.dependency_task_ids = [dependency.id]
    candidate.executions[0].status = TaskStatus.RUNNING
    candidate.executions[0].current_node = CurrentNode.WAITING_DEPENDENCIES
    candidate.executions[0].started_at = None
    candidate.executions[0].finished_at = None
    store.save(candidate)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    observed = []

    def _capture_flow(current: Task):
        execution = service.execution_service.active(current)
        observed.append((execution.started_at, current.context.summary))
        current.task_status = TaskStatus.BLOCKED
        return service._save(current)

    monkeypatch.setattr(service, "_run_automatic_flow", _capture_flow)

    service._resume_unblocked_tasks()

    assert len(observed) == 1
    assert observed[0][0] is not None
    assert "前置任务" in observed[0][1]


def test_rerun_waiting_for_dependencies_is_created_but_not_started(tmp_path) -> None:
    store = InMemoryTaskStore()
    task = _finished_task()
    task.dependency_task_ids = ["dependency_missing"]
    store.save(task)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    response = service.create_rerun(
        task.id,
        _rerun_payload(execution_mode="async"),
        _actor(),
        "dependency-key",
    )

    assert response.task.current_node == CurrentNode.WAITING_DEPENDENCIES
    assert response.execution.current_node == CurrentNode.WAITING_DEPENDENCIES
    assert response.execution.started_at is None
    returned = service.run_confirmed_task(
        task.id,
        expected_execution_id=response.execution.id,
    )
    assert returned.current_node == CurrentNode.WAITING_DEPENDENCIES
    assert returned.executions[-1].started_at is None


def test_find_subtask_allows_legacy_alias_only_without_execution_id(tmp_path) -> None:
    task = _finished_task()
    legacy = SubTask(
        id="legacy_internal_id",
        execution_id="",
        logical_key="legacy_approval",
        title="Legacy approval",
        description="Legacy approval",
        assignee_type="human",
    )
    task.context = TaskContext(
        rounds=[TaskRound(round_index=1, subtasks=[legacy])]
    )
    store = InMemoryTaskStore()
    store.save(task)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    found_task, _, found = service._find_subtask("legacy_approval")

    assert found_task.id == task.id
    assert found is legacy


@pytest.mark.parametrize(
    "submitted_id",
    ["approval", "task_1_approval", "task_1_execution_1_approval"],
)
def test_current_execution_subtask_rejects_legacy_or_old_execution_ids_without_mutation(
    tmp_path,
    submitted_id: str,
) -> None:
    task = _finished_task()
    _rename_task_id(task, "task_d137cc8190fe")
    ExecutionService().create_rerun(
        task,
        _rerun_payload(),
        _actor(),
        idempotency_key="alias-isolation-key",
        request_fingerprint="alias-isolation-fingerprint",
        start_node=CurrentNode.DISPATCH_DECISION,
    )
    execution_id = task.active_execution_id
    target = SubTask(
        id=scoped_subtask_id(task.id, execution_id or "", "approval"),
        execution_id=execution_id,
        logical_key="approval",
        title="Approval",
        description="Approval",
        assignee_type="human",
    )
    blocker = SubTask(
        id=scoped_subtask_id(task.id, execution_id or "", "blocker"),
        execution_id=execution_id,
        logical_key="blocker",
        title="Blocker",
        description="Blocker",
        assignee_type="human",
    )
    task.current_node = CurrentNode.HUMAN_EXECUTION
    task.context = TaskContext(
        rounds=[TaskRound(round_index=1, subtasks=[target, blocker])]
    )
    client = _rerun_client(tmp_path, task)
    before = client.app.state.task_service.get_task(task.id).model_dump_json()

    response = client.post(
        f"/api/v1/subtasks/{submitted_id}/result",
        json={"result_status": "succeeded", "output": "must not apply"},
    )

    assert response.status_code in {404, 409}
    assert client.app.state.task_service.get_task(task.id).model_dump_json() == before


def test_task_service_reads_workflow_from_active_execution_snapshot(tmp_path) -> None:
    task = _finished_task()
    task.task_type = TaskType.MANUAL_ORCHESTRATION
    task.request_metadata = {
        "execution_mode": "workflow_template",
        "workflow_definition": {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "externally_changed", "type": "human"},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"from": "start", "to": "externally_changed"},
                {"from": "externally_changed", "to": "end"},
            ],
        },
    }
    task.executions[0].workflow_snapshot = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "snapshotted_node", "type": "human"},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"from": "start", "to": "snapshotted_node"},
            {"from": "snapshotted_node", "to": "end"},
        ],
    }
    store = InMemoryTaskStore()
    store.save(task)
    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))
    response = service.create_rerun(task.id, _rerun_payload(), _actor(), "workflow-key")

    workflow = service._get_task_workflow(response.task)

    assert [node.id for node in workflow.definition.nodes] == [
        "start",
        "snapshotted_node",
        "end",
    ]


def test_workflow_subtask_ids_change_per_execution_but_keep_logical_key() -> None:
    task = _finished_task()
    node = WorkflowNode(
        id="approval",
        type="human",
        title="Approve",
    )

    first = WorkflowTemplateRunner._node_to_subtask(task, node)
    ExecutionService().create_rerun(
        task,
        _rerun_payload(),
        _actor(),
        idempotency_key="workflow-subtask-key",
        request_fingerprint="workflow-subtask-fingerprint",
        start_node=CurrentNode.DISPATCH_DECISION,
    )
    second = WorkflowTemplateRunner._node_to_subtask(task, node)

    assert first.id.startswith("subtask_")
    assert second.id.startswith("subtask_")
    assert len(first.id) <= 64
    assert len(second.id) <= 64
    assert second.id != first.id
    assert first.logical_key == second.logical_key == "approval"
    assert first.execution_id == "execution_1"
    assert second.execution_id == task.active_execution_id


def test_task_graph_binds_auto_planned_subtask_to_active_execution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    _rename_task_id(task, "task_d137cc8190fe")
    ExecutionService().create_rerun(
        task,
        _rerun_payload(),
        _actor(),
        idempotency_key="auto-subtask-key",
        request_fingerprint="auto-subtask-fingerprint",
        start_node=CurrentNode.DISPATCH_DECISION,
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.plan_next_round_with_model",
        lambda _task, _agents: RoundPlan(
            should_continue=True,
            subtasks=[
                SubTask(
                    id="temporary-model-id-that-is-long-from-llm",
                    title="Prepare",
                    description="Prepare delivery",
                )
            ],
        ),
    )
    runner = TaskGraphRunner(AgentRegistry(tmp_path / "agents.json"))

    state = runner._round_dispatch(
        {
            "task": task,
            "round_plan": RoundPlan(should_continue=False),
            "round_outputs": [],
            "paused": False,
        }
    )

    subtask = state["round_plan"].subtasks[0]
    assert subtask.execution_id == task.active_execution_id
    assert subtask.logical_key == "temporary-model-id-that-is-long-from-llm"
    assert len(subtask.id) <= 64


def test_task_graph_human_gate_subtask_id_fits_database_column_on_rerun(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    _rename_task_id(task, "task_d137cc8190fe")
    task.draft = TaskDraft(
        title="需要人工确认",
        description="请人工确认后继续。",
        confidence=1.0,
        suggested_assignee_type="human",
    )
    ExecutionService().create_rerun(
        task,
        _rerun_payload(),
        _actor(),
        idempotency_key="human-gate-subtask-key",
        request_fingerprint="human-gate-subtask-fingerprint",
        start_node=CurrentNode.DISPATCH_DECISION,
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.plan_next_round_with_model",
        lambda _task, _agents: RoundPlan(should_continue=False, subtasks=[]),
    )
    runner = TaskGraphRunner(AgentRegistry(tmp_path / "agents.json"))

    state = runner._round_dispatch(
        {
            "task": task,
            "round_plan": RoundPlan(should_continue=False),
            "round_outputs": [],
            "paused": False,
        }
    )

    subtask = state["round_plan"].subtasks[0]
    assert subtask.assignee_type == "human"
    assert subtask.logical_key == "human_review"
    assert len(subtask.id) <= 64


def _rerun_client(tmp_path, task: Task | None = None) -> TestClient:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    if task is not None:
        client.app.state.task_store.save(task)
    return client


def _create_user(client: TestClient, name: str) -> dict:
    return client.post(
        "/api/v1/users",
        json={
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}@example.com",
            "role": "user",
        },
    ).json()


def test_execution_history_api_supports_owner_and_admin_and_forbids_other_user(
    tmp_path,
) -> None:
    client = _rerun_client(tmp_path)
    owner = _create_user(client, "Task Owner")
    other = _create_user(client, "Other User")
    task = _finished_task()
    task.created_by_user_id = owner["id"]
    task.created_by_user_name = owner["name"]
    client.app.state.task_store.save(task)

    owner_list = client.get(
        f"/api/v1/tasks/{task.id}/executions",
        headers={"X-User-Id": owner["id"]},
    )
    admin_detail = client.get(
        f"/api/v1/tasks/{task.id}/executions/execution_1"
    )
    forbidden = client.get(
        f"/api/v1/tasks/{task.id}/executions",
        headers={"X-User-Id": other["id"]},
    )

    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()] == ["execution_1"]
    assert admin_detail.status_code == 200
    assert admin_detail.json()["id"] == "execution_1"
    assert forbidden.status_code == 403


def test_rerun_preflight_api_returns_side_effects_and_missing_source_is_404(
    tmp_path,
) -> None:
    task = _finished_task(
        tool_results=[
            ToolExecutionResult(
                tool_execution_id="tool_email",
                tool_name="send_email",
                tool_type="smtp_email",
                side_effect=True,
                side_effect_known=True,
                success=True,
                result="sent",
            )
        ]
    )
    client = _rerun_client(tmp_path, task)

    response = client.post(
        f"/api/v1/tasks/{task.id}/executions/preflight",
        json={"source_execution_id": "execution_1"},
    )
    missing = client.post(
        f"/api/v1/tasks/{task.id}/executions/preflight",
        json={"source_execution_id": "execution_missing"},
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert response.json()["requires_side_effect_confirmation"] is True
    assert response.json()["side_effects"][0]["tool_execution_id"] == "tool_email"
    assert response.json()["next_attempt_no"] == 2
    assert response.json()["dependencies_satisfied"] is True
    assert response.json()["start_node"] == "dispatch_decision"
    assert response.json()["will_wait_for_dependencies"] is False
    assert missing.status_code == 404


def test_rerun_preflight_api_reports_dependency_waiting_start_node(tmp_path) -> None:
    task = _finished_task()
    task.dependency_task_ids = ["dependency_missing"]
    client = _rerun_client(tmp_path, task)

    response = client.post(
        f"/api/v1/tasks/{task.id}/executions/preflight",
        json={"source_execution_id": "execution_1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["next_attempt_no"] == 2
    assert body["dependencies_satisfied"] is False
    assert body["start_node"] == "waiting_dependencies"
    assert body["will_wait_for_dependencies"] is True


def test_rerun_api_rejects_active_loop_count_mismatch_without_mutation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    task.executions[0].loop_count = task.loop_count + 1
    client = _rerun_client(tmp_path, task)
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    before = client.app.state.task_service.get_task(task.id).model_dump_json()

    preflight = client.post(
        f"/api/v1/tasks/{task.id}/executions/preflight",
        json={"source_execution_id": "execution_1"},
    )
    created = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={
            "source_execution_id": "execution_1",
            "reason": "Retry inconsistent projection",
            "execution_mode": "async",
        },
        headers={"Idempotency-Key": "api-loop-count-mismatch-key"},
    )

    assert preflight.status_code == 200
    assert preflight.json()["allowed"] is False
    assert [issue["code"] for issue in preflight.json()["issues"]] == [
        "active_loop_count_mismatch"
    ]
    assert created.status_code == 409
    assert [issue["code"] for issue in created.json()["detail"]["issues"]] == [
        "active_loop_count_mismatch"
    ]
    assert client.app.state.task_service.get_task(task.id).model_dump_json() == before


def test_async_rerun_api_returns_201_then_idempotent_200_and_schedules_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    client = _rerun_client(tmp_path, task)
    starts: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda task_id, expected_execution_id=None: starts.append(
            (task_id, expected_execution_id)
        ),
    )
    payload = {
        "source_execution_id": "execution_1",
        "reason": "Retry asynchronously",
        "execution_mode": "async",
    }
    headers = {"Idempotency-Key": "api-rerun-key"}

    first = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers=headers,
    )
    replay = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 201
    assert first.json()["replayed"] is False
    assert first.json()["execution_is_active"] is True
    assert first.json()["scheduled"] is True
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["execution_is_active"] is True
    assert replay.json()["scheduled"] is False
    assert replay.json()["execution"]["id"] == first.json()["execution"]["id"]
    assert starts == [(task.id, first.json()["execution"]["id"])]
    assert len(client.app.state.task_service.get_task(task.id).executions) == 2


def test_replaying_old_key_returns_latest_task_and_marks_execution_inactive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    client = _rerun_client(tmp_path, task)
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    payload = {
        "source_execution_id": "execution_1",
        "reason": "Retry",
        "execution_mode": "async",
    }
    first = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "key-a"},
    ).json()
    service = client.app.state.task_service
    current = service.get_task(task.id)
    service.completion_service.finalize(
        current,
        candidate_status=TaskStatus.FAILED,
        output="Attempt A stopped",
        reason="Attempt A stopped",
    )
    service._save(current)
    second = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "key-b"},
    ).json()

    replay = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "key-a"},
    )

    assert replay.status_code == 200
    body = replay.json()
    assert body["execution"]["id"] == first["execution"]["id"]
    assert body["execution_is_active"] is False
    assert body["task"]["active_execution_id"] == second["execution"]["id"]
    assert body["task"]["active_execution_id"] != body["execution"]["id"]


def test_execution_apis_redact_sensitive_arguments_without_mutating_history(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_values = {
        "password": "secret-password-value",
        "token": "secret-token-value",
        "authorization": "Bearer secret-authorization-value",
        "body": "secret-body-value",
        "content": "secret-content-value",
    }
    tool_result = ToolExecutionResult(
        tool_execution_id="tool_sensitive",
        tool_name="sensitive_http",
        tool_type="http",
        side_effect=True,
        side_effect_known=True,
        arguments=dict(reversed(list(secret_values.items()))),
        success=True,
        result="created",
    )
    task = _finished_task(tool_results=[tool_result])
    subtask = task.context.rounds[0].subtasks[0]
    subtask.tool_calls = [
        core_models.ToolCall(
            tool_name="sensitive_http",
            arguments=secret_values,
        )
    ]
    task.executions[0].context_snapshot = task.context.model_copy(deep=True)
    artifact = Artifact(
        id="artifact_sensitive",
        task_id=task.id,
        execution_id="execution_1",
        kind=ArtifactKind.TOOL_RESULT,
        source_type=ArtifactSourceType.TOOL_RESULT,
        source_id="tool_sensitive",
        name="Sensitive receipt",
        content="created",
        validation_status=ArtifactValidationStatus.VALID,
        metadata={"arguments": secret_values, "tool_name": "sensitive_http"},
        created_at=utc_now(),
    )
    task.artifacts = [artifact]
    task.executions[0].artifacts = [artifact.model_copy(deep=True)]
    client = _rerun_client(tmp_path, task)
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    before = client.app.state.task_service.get_task(task.id).model_dump_json()

    read_responses = [
        client.get(f"/api/v1/tasks/{task.id}/executions"),
        client.get(f"/api/v1/tasks/{task.id}/executions/execution_1"),
        client.post(
            f"/api/v1/tasks/{task.id}/executions/preflight",
            json={"source_execution_id": "execution_1"},
        ),
        client.post(
            f"/api/v1/tasks/{task.id}/executions",
            json={
                "source_execution_id": "execution_1",
                "reason": "Sensitive rerun",
                "execution_mode": "async",
            },
            headers={"Idempotency-Key": "sensitive-unconfirmed-key"},
        ),
    ]
    assert [response.status_code for response in read_responses] == [200, 200, 200, 428]
    assert client.app.state.task_service.get_task(task.id).model_dump_json() == before
    confirmed = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={
            "source_execution_id": "execution_1",
            "reason": "Sensitive rerun",
            "execution_mode": "async",
            "confirm_side_effects": True,
        },
        headers={"Idempotency-Key": "sensitive-confirmed-key"},
    )
    responses = [*read_responses, confirmed]

    assert [response.status_code for response in responses] == [200, 200, 200, 428, 201]
    for response in responses:
        for secret in secret_values.values():
            assert secret not in response.text
    preflight = responses[2].json()
    assert preflight["side_effects"][0]["argument_keys"] == sorted(secret_values)
    assert "arguments" not in preflight["side_effects"][0]
    assert client.app.state.task_service.get_task(task.id).model_dump_json() != before
    domain = client.app.state.task_service.get_task(task.id)
    source = domain.executions[0]
    assert source.context_snapshot.rounds[0].subtasks[0].tool_results[0].arguments == dict(
        reversed(list(secret_values.items()))
    )
    assert source.artifacts[0].metadata["arguments"] == secret_values


def test_rerun_api_maps_running_and_idempotency_conflicts_to_409(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    client = _rerun_client(tmp_path, task)
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    first_payload = {
        "source_execution_id": "execution_1",
        "reason": "First retry",
        "execution_mode": "async",
    }
    first = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=first_payload,
        headers={"Idempotency-Key": "same-key"},
    )

    idempotency_conflict = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={**first_payload, "reason": "Changed retry"},
        headers={"Idempotency-Key": "same-key"},
    )
    running_conflict = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=first_payload,
        headers={"Idempotency-Key": "different-key"},
    )

    assert first.status_code == 201
    assert idempotency_conflict.status_code == 409
    assert running_conflict.status_code == 409


@pytest.mark.parametrize(
    ("headers", "payload"),
    [
        ({}, {"source_execution_id": "execution_1", "reason": "retry"}),
        (
            {"Idempotency-Key": "   "},
            {"source_execution_id": "execution_1", "reason": "retry"},
        ),
        (
            {"Idempotency-Key": "key"},
            {"source_execution_id": "execution_1", "reason": "   "},
        ),
    ],
)
def test_rerun_api_rejects_missing_or_empty_key_and_reason_with_422(
    tmp_path,
    headers: dict,
    payload: dict,
) -> None:
    task = _finished_task()
    client = _rerun_client(tmp_path, task)

    response = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 422
    assert len(client.app.state.task_service.get_task(task.id).executions) == 1


def test_rerun_api_requires_428_side_effect_confirmation_and_records_user(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task(
        tool_results=[
            ToolExecutionResult(
                tool_execution_id="tool_file",
                tool_name="write_file",
                tool_type="file_write",
                side_effect=True,
                side_effect_known=True,
                success=False,
                error="unknown outcome",
            )
        ]
    )
    client = _rerun_client(tmp_path, task)
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    payload = {
        "source_execution_id": "execution_1",
        "reason": "Retry file generation",
        "execution_mode": "async",
    }

    required = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json=payload,
        headers={"Idempotency-Key": "side-effect-key"},
    )
    confirmed = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={**payload, "confirm_side_effects": True},
        headers={"Idempotency-Key": "confirmed-side-effect-key"},
    )

    assert required.status_code == 428
    assert confirmed.status_code == 201
    execution = confirmed.json()["execution"]
    assert execution["side_effects_confirmed_by_user_id"] == "root"
    assert execution["side_effects_confirmed_by_user_name"]
    assert execution["side_effects_confirmed_at"]


def test_dependency_waiting_async_rerun_api_does_not_schedule(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    task.dependency_task_ids = ["missing_dependency"]
    client = _rerun_client(tmp_path, task)
    starts = []
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    response = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={
            "source_execution_id": "execution_1",
            "reason": "Retry when dependency completes",
            "execution_mode": "async",
        },
        headers={"Idempotency-Key": "dependency-api-key"},
    )

    assert response.status_code == 201
    assert response.json()["scheduled"] is False
    assert response.json()["task"]["current_node"] == "waiting_dependencies"
    assert response.json()["execution"]["started_at"] is None
    assert starts == []


def test_sync_rerun_api_runs_expected_execution_without_scheduling(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _finished_task()
    client = _rerun_client(tmp_path, task)
    calls = []

    def _capture_run(task_id, expected_execution_id=None):
        calls.append((task_id, expected_execution_id))
        return client.app.state.task_service.get_task(task_id)

    monkeypatch.setattr(
        client.app.state.task_service,
        "run_confirmed_task",
        _capture_run,
    )
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sync rerun must not schedule background work")
        ),
    )

    response = client.post(
        f"/api/v1/tasks/{task.id}/executions",
        json={
            "source_execution_id": "execution_1",
            "reason": "Retry synchronously",
            "execution_mode": "sync",
        },
        headers={"Idempotency-Key": "sync-api-key"},
    )

    assert response.status_code == 201
    assert response.json()["scheduled"] is False
    assert calls == [(task.id, response.json()["execution"]["id"])]


def test_rerun_api_returns_404_for_missing_task_and_source(tmp_path) -> None:
    client = _rerun_client(tmp_path, _finished_task())
    payload = {
        "source_execution_id": "execution_missing",
        "reason": "Retry",
    }

    missing_task = client.post(
        "/api/v1/tasks/task_missing/executions",
        json=payload,
        headers={"Idempotency-Key": "missing-task-key"},
    )
    missing_source = client.post(
        "/api/v1/tasks/task_1/executions",
        json=payload,
        headers={"Idempotency-Key": "missing-source-key"},
    )

    assert missing_task.status_code == 404
    assert missing_source.status_code == 404
