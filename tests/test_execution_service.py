from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.core.enums import CurrentNode, ExecutionTriggerType, SourceType, TaskStatus
from app.core.models import (
    Task,
    TaskContext,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    SubTask,
    User,
    utc_now,
)
from app.services.execution_service import ExecutionProjectionConflictError, ExecutionService
from app.services.artifact_service import ArtifactRegistrationClosedError, ArtifactService


def _actor() -> User:
    now = utc_now()
    return User(id="user_1", name="Tester", created_at=now, updated_at=now)


def _task() -> Task:
    now = utc_now()
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery plan",
        request_metadata={"workflow_definition": {"nodes": [{"id": "start"}], "edges": []}},
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=TaskContract(
            goal="Prepare a plan",
            deliverable_goal="Delivery plan",
            success_criteria=[TaskContractItem(id="criterion_1", description="Reviewable")],
            confirmed_at=now,
        ),
        context=TaskContext(summary="initial context", artifacts=["input.txt"]),
        created_at=now,
        updated_at=now,
    )


def _execution(task_id: str = "task_1", execution_id: str = "execution_1", attempt_no: int = 1) -> dict:
    now = utc_now()
    return TaskExecution(
        id=execution_id,
        task_id=task_id,
        attempt_no=attempt_no,
        trigger_type=ExecutionTriggerType.INITIAL,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    ).model_dump(mode="json")


def _task_data_with_executions(executions: list[dict], active_execution_id: str | None) -> dict:
    data = _task().model_dump(mode="json")
    data["executions"] = executions
    data["active_execution_id"] = active_execution_id
    return data


def test_create_initial_keeps_snapshots_isolated_and_starts_only_when_marked() -> None:
    task = _task()
    service = ExecutionService()

    execution = service.create_initial(task, _actor(), start_node=CurrentNode.DISPATCH_DECISION)

    assert execution.started_at is None
    task.contract.goal = "changed goal"
    task.request_metadata["workflow_definition"]["nodes"].append({"id": "changed"})
    task.context.summary = "changed context"
    assert execution.contract_snapshot.goal == "Prepare a plan"
    assert execution.workflow_snapshot == {"nodes": [{"id": "start"}], "edges": []}
    assert execution.context_snapshot.summary == "initial context"

    service.mark_started(task)
    started_at = execution.started_at
    service.mark_started(task)
    assert started_at is not None
    assert execution.started_at == started_at

    task.context.summary = "projected context"
    task.completion_report = {"checks": ["passed"]}
    service.sync_projection(task)
    task.context.summary = "changed after sync"
    task.completion_report["checks"].append("mutated")
    assert execution.context_snapshot.summary == "projected context"
    assert execution.completion_report == {"checks": ["passed"]}


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("task_status", TaskStatus.FAILED),
        ("current_node", CurrentNode.HUMAN_INTERVENTION),
        ("context", TaskContext(summary="changed")),
        ("final_output", "changed output"),
        ("loop_count", 2),
        ("completion_report", {"result": "changed"}),
    ],
)
def test_finished_execution_is_idempotent_but_rejects_projection_overwrite(
    field_name: str,
    changed_value,
) -> None:
    task = _task()
    service = ExecutionService()
    execution = service.create_initial(task, _actor(), start_node=CurrentNode.DISPATCH_DECISION)
    task.task_status = TaskStatus.SUCCEEDED
    task.current_node = CurrentNode.COMPLETION_JUDGE
    task.context = TaskContext(summary="completed context")
    task.final_output = "completed output"
    task.loop_count = 1
    task.completion_report = {"result": "accepted"}
    service.sync_projection(task)
    sealed = execution.model_copy(deep=True)

    assert execution.finished_at is not None
    assert service.sync_projection(task) is execution

    setattr(task, field_name, deepcopy(changed_value))
    with pytest.raises(ExecutionProjectionConflictError, match="finished execution .* cannot be overwritten"):
        service.sync_projection(task)

    assert execution == sealed


def test_task_rejects_duplicate_execution_ids() -> None:
    with pytest.raises(ValidationError, match="execution IDs must be unique"):
        Task.model_validate(
            _task_data_with_executions(
                [_execution(), _execution(execution_id="execution_1", attempt_no=2)],
                "execution_1",
            )
        )


def test_task_rejects_duplicate_execution_attempt_numbers() -> None:
    with pytest.raises(ValidationError, match="execution attempt numbers must be unique"):
        Task.model_validate(
            _task_data_with_executions(
                [_execution(), _execution(execution_id="execution_2")],
                "execution_1",
            )
        )


def test_task_rejects_execution_for_another_task() -> None:
    with pytest.raises(ValidationError, match="execution task_id must match task id"):
        Task.model_validate(
            _task_data_with_executions([_execution(task_id="task_other")], "execution_1")
        )


@pytest.mark.parametrize(
    ("executions", "active_execution_id"),
    [
        ([], "execution_missing"),
        ([_execution()], None),
        ([_execution()], "execution_missing"),
    ],
)
def test_task_rejects_invalid_active_execution_reference(
    executions: list[dict],
    active_execution_id: str | None,
) -> None:
    with pytest.raises(ValidationError, match="active_execution_id must reference an execution"):
        Task.model_validate(_task_data_with_executions(executions, active_execution_id))


@pytest.mark.parametrize("mutation", ["append", "modify"])
def test_finished_execution_rejects_top_level_artifact_changes(mutation: str) -> None:
    task = _task()
    execution_service = ExecutionService()
    execution_service.create_initial(task, _actor(), start_node=CurrentNode.DISPATCH_DECISION)
    artifact_service = ArtifactService()
    artifact = artifact_service.register_task_output(task, "Completed delivery")
    assert artifact is not None
    task.task_status = TaskStatus.SUCCEEDED
    task.current_node = CurrentNode.COMPLETION_JUDGE
    task.final_output = "Completed delivery"
    execution_service.sync_projection(task)

    if mutation == "append":
        before = task.model_dump(mode="json")
        with pytest.raises(ArtifactRegistrationClosedError, match="closed"):
            artifact_service.register_subtask_output(
                task,
                SubTask(id="late_subtask", title="Late", description="Late output"),
                "Late artifact",
            )
        assert task.model_dump(mode="json") == before
        return
    else:
        task.artifacts[0].content = "Changed after seal"

    with pytest.raises(ExecutionProjectionConflictError, match="cannot be overwritten"):
        execution_service.sync_projection(task)
