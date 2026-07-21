import pytest

import app.workflows.task_graph as task_graph_module
import app.workflows.template_runner as template_runner_module
from app.core.enums import (
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    RoundPlan,
    SubTask,
    Task,
    TaskExecution,
    WorkflowNode,
    utc_now,
)
from app.workflows.subtask_identity import build_subtask_id
from app.workflows.task_graph import TaskGraphRunner
from app.workflows.template_runner import WorkflowTemplateRunner


OVERFLOW_TASK_ID = "task_6453d6f88b00"
OVERFLOW_EXECUTION_ID = "execution_77c4f462ec88"
OVERFLOW_LOGICAL_KEY = "agent_agent_requirement_anal_1"


def _task_with_execution(task_id: str, execution_id: str) -> Task:
    now = utc_now()
    execution = TaskExecution(
        id=execution_id,
        task_id=task_id,
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    )
    return Task(
        id=task_id,
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Analyze requirement",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        executions=[execution],
        active_execution_id=execution_id,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("execution_id", "expected"),
    [
        ("execution_1", "task_1_execution_1_approval"),
        ("", "task_1_approval"),
    ],
)
def test_build_subtask_id_preserves_short_legacy_identity(
    execution_id: str,
    expected: str,
) -> None:
    assert build_subtask_id("task_1", execution_id, "approval") == expected


def test_build_subtask_id_shortens_real_overflow_case_deterministically() -> None:
    raw_id = (
        f"{OVERFLOW_TASK_ID}_{OVERFLOW_EXECUTION_ID}_{OVERFLOW_LOGICAL_KEY}"
    )
    assert len(raw_id) == 71

    first = build_subtask_id(
        OVERFLOW_TASK_ID,
        OVERFLOW_EXECUTION_ID,
        OVERFLOW_LOGICAL_KEY,
    )
    second = build_subtask_id(
        OVERFLOW_TASK_ID,
        OVERFLOW_EXECUTION_ID,
        OVERFLOW_LOGICAL_KEY,
    )

    assert first == second
    assert len(first) <= 64


def test_build_subtask_id_handles_surrogate_in_long_identity() -> None:
    logical_key = "\ud800" * 65

    first = build_subtask_id("task_1", "execution_1", logical_key)
    second = build_subtask_id("task_1", "execution_1", logical_key)

    assert first == second
    assert len(first) <= 64


@pytest.mark.parametrize(
    "logical_key",
    ["review/approval", "review?mode=approval", "review\x00approval", "\ud800"],
)
def test_build_subtask_id_hashes_unsafe_short_identity(logical_key: str) -> None:
    subtask_id = build_subtask_id("task_1", "execution_1", logical_key)

    assert subtask_id.startswith("subtask_")
    assert len(subtask_id) <= 64
    assert subtask_id.isascii()


def test_build_subtask_id_distinguishes_execution_and_logical_key() -> None:
    split_execution_id = f"{OVERFLOW_EXECUTION_ID}_agent"
    split_logical_key = "agent_requirement_anal_1"
    assert (
        f"{OVERFLOW_TASK_ID}_{split_execution_id}_{split_logical_key}"
        == f"{OVERFLOW_TASK_ID}_{OVERFLOW_EXECUTION_ID}_{OVERFLOW_LOGICAL_KEY}"
    )

    identities = {
        build_subtask_id(
            OVERFLOW_TASK_ID,
            OVERFLOW_EXECUTION_ID,
            OVERFLOW_LOGICAL_KEY,
        ),
        build_subtask_id(
            OVERFLOW_TASK_ID,
            split_execution_id,
            split_logical_key,
        ),
        build_subtask_id(
            OVERFLOW_TASK_ID,
            OVERFLOW_EXECUTION_ID,
            f"{OVERFLOW_LOGICAL_KEY}_alternate",
        ),
    }

    assert len(identities) == 3
    assert all(len(identity) <= 64 for identity in identities)


def test_template_node_uses_shared_subtask_identity_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_execution(OVERFLOW_TASK_ID, OVERFLOW_EXECUTION_ID)
    calls = []

    def _build(task_id: str, execution_id: str, logical_key: str) -> str:
        calls.append((task_id, execution_id, logical_key))
        return "shared-template-subtask-id"

    monkeypatch.setattr(
        template_runner_module,
        "build_subtask_id",
        _build,
    )

    subtask = WorkflowTemplateRunner._node_to_subtask(
        task,
        WorkflowNode(id=OVERFLOW_LOGICAL_KEY, type="agent"),
    )

    assert subtask.id == "shared-template-subtask-id"
    assert subtask.logical_key == OVERFLOW_LOGICAL_KEY
    assert calls == [
        (OVERFLOW_TASK_ID, OVERFLOW_EXECUTION_ID, OVERFLOW_LOGICAL_KEY)
    ]


def test_auto_planning_uses_shared_subtask_identity_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_execution(OVERFLOW_TASK_ID, OVERFLOW_EXECUTION_ID)
    plan = RoundPlan(
        subtasks=[
            SubTask(
                id=OVERFLOW_LOGICAL_KEY,
                title="Analyze",
                description="Analyze requirement",
            )
        ]
    )
    calls = []

    def _build(task_id: str, execution_id: str, logical_key: str) -> str:
        calls.append((task_id, execution_id, logical_key))
        return "shared-auto-planned-subtask-id"

    monkeypatch.setattr(
        task_graph_module,
        "build_subtask_id",
        _build,
    )

    TaskGraphRunner._bind_planned_subtasks(task, plan)

    subtask = plan.subtasks[0]
    assert subtask.id == "shared-auto-planned-subtask-id"
    assert subtask.logical_key == OVERFLOW_LOGICAL_KEY
    assert calls == [
        (OVERFLOW_TASK_ID, OVERFLOW_EXECUTION_ID, OVERFLOW_LOGICAL_KEY)
    ]


def test_template_and_auto_planned_overflow_ids_fit_storage_limit() -> None:
    task = _task_with_execution(OVERFLOW_TASK_ID, OVERFLOW_EXECUTION_ID)
    template_subtask = WorkflowTemplateRunner._node_to_subtask(
        task,
        WorkflowNode(id=OVERFLOW_LOGICAL_KEY, type="agent"),
    )
    plan = RoundPlan(
        subtasks=[
            SubTask(
                id=OVERFLOW_LOGICAL_KEY,
                title="Analyze",
                description="Analyze requirement",
            )
        ]
    )

    TaskGraphRunner._bind_planned_subtasks(task, plan)

    expected_id = build_subtask_id(
        OVERFLOW_TASK_ID,
        OVERFLOW_EXECUTION_ID,
        OVERFLOW_LOGICAL_KEY,
    )
    assert template_subtask.id == expected_id
    assert plan.subtasks[0].id == expected_id
    assert len(expected_id) <= 64
    assert template_subtask.logical_key == OVERFLOW_LOGICAL_KEY
    assert plan.subtasks[0].logical_key == OVERFLOW_LOGICAL_KEY
