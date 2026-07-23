from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, Thread

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.enums import (
    CriterionResultStatus,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
    TaskType,
)
from app.main import create_app
from app.core.models import (
    CompletionReport,
    CriterionResult,
    ExecutionResultCreate,
    Task,
    TaskConfirm,
    TaskContract,
    TaskContractInput,
    TaskContractItem,
    TaskContext,
    TaskDraft,
    TaskExecution,
    TaskRequestCreate,
    TaskRound,
    SubTask,
    utc_now,
)
from app.services.artifact_service import ArtifactService
from app.services.deliverable_materializer import (
    DeliverableMaterializer,
    MaterializedDeliverable,
)
from app.services.storage import AgentRegistry, InMemoryTaskStore
from app.services.task_contract_service import TaskContractService
from app.services.task_service import TaskService


def test_task_request_waits_for_human_confirmation(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户A报价任务",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["request_id"].startswith("req_")
    assert len(result["tasks"]) == 1
    task = result["tasks"][0]
    assert task["task_status"] == "running"
    assert task["current_node"] == "human_confirmation"
    assert task["title"] == "客户A报价任务"
    assert task["description"] == "Create a quote for customer A"
    assert task["content"] == "Create a quote for customer A"
    assert task["draft"]["title"] == "Create a quote for customer A"
    assert task["task_type"] == "auto_planning"
    assert task["initial_context"] == task["context"]
    assert task["executions"] == []
    assert task["active_execution_id"] is None
    assert task["completion_report"] is None


def test_contract_normalization_promotes_legacy_requirements_idempotently() -> None:
    contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Legacy file delivery",
        deliverable_kind="file",
        deliverable_format="text",
        deliverable_filename="legacy.txt",
        deliverable_requirements=[
            TaskContractItem(id=f"requirement_{index}", description=f"Requirement {index}")
            for index in range(1, 7)
        ],
        success_criteria=[
            TaskContractItem(id="criterion_duplicate", description="requirement 1"),
            TaskContractItem(id="requirement_2", description="Visible criterion 1"),
            *[
                TaskContractItem(
                    id=f"criterion_{index}",
                    description=f"Visible criterion {index}",
                )
                for index in range(2, 7)
            ],
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )

    normalized = TaskContractService().normalize_contract(contract)

    assert normalized.deliverable_kind == "file"
    assert normalized.deliverable_format == "text"
    assert normalized.deliverable_filename == "legacy.txt"
    assert normalized.deliverable_requirements == []
    assert normalized.requires_human_acceptance is False
    descriptions = [item.description for item in normalized.success_criteria]
    assert descriptions[:9] == [
        *[f"Requirement {index}" for index in range(1, 7)],
        *[f"Visible criterion {index}" for index in range(1, 4)],
    ]
    assert descriptions[9].startswith("同时满足以下历史验收标准：")
    assert all(
        description in descriptions[9]
        for description in (
            "Visible criterion 4",
            "Visible criterion 5",
            "Visible criterion 6",
        )
    )
    assert len({item.id for item in normalized.success_criteria}) == 10
    assert normalized.success_criteria[6].id != "requirement_2"
    assert TaskContractService().normalize_contract(normalized) == normalized


def test_contract_normalization_avoids_aggregate_description_collision() -> None:
    overflow_descriptions = ["Overflow condition A", "Overflow condition B"]
    colliding_description = (
        "同时满足以下历史验收标准："
        + "；".join(overflow_descriptions)
    )
    contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        deliverable_requirements=[
            TaskContractItem(
                id="requirement_collision",
                description=colliding_description,
            ),
            *[
                TaskContractItem(
                    id=f"requirement_{index}",
                    description=f"Requirement {index}",
                )
                for index in range(2, 10)
            ],
        ],
        success_criteria=[
            TaskContractItem(
                id=f"criterion_overflow_{index}",
                description=description,
            )
            for index, description in enumerate(overflow_descriptions, start=1)
        ],
        confirmed_at=utc_now(),
    )

    normalized = TaskContractService().normalize_contract(contract)
    descriptions = [item.description for item in normalized.success_criteria]

    assert len(descriptions) == 10
    assert len({description.casefold() for description in descriptions}) == 10
    assert all(
        description in descriptions[-1]
        for description in overflow_descriptions
    )
    assert TaskContractService().normalize_contract(normalized) == normalized


def _legacy_pending_acceptance_task(
    *,
    task_id: str,
    include_requirement_evidence: bool,
    output: str = "Legacy reviewable output",
) -> Task:
    now = utc_now()
    contract = TaskContract(
        goal="Prepare legacy delivery",
        deliverable_goal="Legacy reviewable file",
        deliverable_kind="file",
        deliverable_format="text",
        deliverable_filename="legacy.txt",
        deliverable_requirements=[
            TaskContractItem(
                id="requirement_summary",
                description="Contains a summary",
            )
        ],
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="The result is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=now,
    )
    criterion_results = [
        CriterionResult(
            criterion_id="criterion_reviewable",
            status=CriterionResultStatus.PASSED,
            evidence_text=output,
        )
    ]
    if include_requirement_evidence:
        criterion_results.insert(
            0,
            CriterionResult(
                criterion_id="requirement_summary",
                status=CriterionResultStatus.PASSED,
                evidence_text=output,
            ),
        )
    execution_id = f"execution_{task_id}"
    report = CompletionReport(
        id=f"completion_{task_id}",
        execution_id=execution_id,
        terminal_status=TaskStatus.RUNNING,
        completion_reason="Awaiting required human acceptance",
        criterion_results=criterion_results,
        human_accepted=False,
        awaiting_human_decision=False,
        decided_by_type="system",
        decided_by_id="completion_service",
        decided_at=now,
        evidence_summary="human acceptance is required",
    )
    context = TaskContext(summary=output)
    execution = TaskExecution(
        id=execution_id,
        task_id=task_id,
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        trigger_reason="Initial task confirmation",
        contract_snapshot=contract.model_copy(deep=True),
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.HUMAN_INTERVENTION,
        context_snapshot=context.model_copy(deep=True),
        final_output=output,
        created_at=now,
        started_at=now,
        completion_report=report.model_copy(deep=True),
    )
    return Task(
        id=task_id,
        request_id=f"request_{task_id}",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Legacy pending acceptance task",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_INTERVENTION,
        title="Legacy pending acceptance task",
        description="Legacy pending acceptance task",
        contract=contract,
        context=context,
        initial_context=TaskContext(),
        executions=[execution],
        active_execution_id=execution_id,
        completion_report=report,
        final_output=output,
        created_at=now,
        updated_at=now,
    )


def test_task_service_migrates_legacy_pending_acceptance_to_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OUTPUT_DIR", str(tmp_path / "outputs"))
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_accepted",
        include_requirement_evidence=True,
    )
    store.save(task)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.SUCCEEDED
    assert migrated.current_node == CurrentNode.COMPLETION_JUDGE
    assert migrated.contract is not None
    assert migrated.contract.deliverable_kind == "file"
    assert migrated.contract.deliverable_requirements == []
    assert migrated.contract.requires_human_acceptance is False
    assert [item.id for item in migrated.contract.success_criteria] == [
        "requirement_summary",
        "criterion_reviewable",
    ]
    assert migrated.executions[0].contract_snapshot is not None
    assert [
        item.id
        for item in migrated.executions[0].contract_snapshot.deliverable_requirements
    ] == ["requirement_summary"]
    assert migrated.executions[0].contract_snapshot.requires_human_acceptance is True
    assert migrated.executions[0].status == TaskStatus.SUCCEEDED
    assert migrated.executions[0].finished_at is not None
    assert migrated.completion_report is not None
    assert migrated.completion_report.terminal_status == TaskStatus.SUCCEEDED
    assert any(event.type == "criteria_only_migrated" for event in migrated.events)


def test_task_service_migration_reuses_file_evidence_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "outputs"
    monkeypatch.setenv("AGENT_OUTPUT_DIR", str(output_root))
    store = InMemoryTaskStore()
    stored_output = "Stored legacy final output"
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_file_evidence",
        include_requirement_evidence=True,
        output=stored_output,
    )
    task.context = TaskContext(
        summary="Context summary must not replace the stored final output",
        rounds=[
            TaskRound(
                round_index=1,
                subtasks=[
                    SubTask(
                        id="subtask_completed",
                        title="Completed work",
                        description="Completed work",
                        status=TaskStatus.SUCCEEDED,
                        output="Completed subtask output",
                    )
                ],
            )
        ],
    )
    task.executions[0].context_snapshot = task.context.model_copy(deep=True)
    delivery_path = output_root / task.id / task.active_execution_id / "legacy.txt"
    delivery_path.parent.mkdir(parents=True)
    delivery_path.write_text(stored_output, encoding="utf-8")
    artifact = ArtifactService().register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content=stored_output,
            media_type="text/plain",
            delivery_format="text",
        ),
    )
    assert task.completion_report is not None
    task.completion_report.artifact_ids = [artifact.id]
    for result in task.completion_report.criterion_results:
        result.evidence_artifact_ids = [artifact.id]
    task.executions[0].artifacts = [artifact.model_copy(deep=True)]
    task.executions[0].completion_report = task.completion_report.model_copy(deep=True)
    store.save(task)
    artifacts_before = [
        item.model_dump(mode="json")
        for item in task.artifacts
    ]
    execution_artifacts_before = [
        item.model_dump(mode="json")
        for item in task.executions[0].artifacts
    ]
    files_before = {
        str(path.relative_to(output_root)): path.read_bytes()
        for path in output_root.rglob("*")
        if path.is_file()
    }

    def fail_side_effect(*_args, **_kwargs):
        raise AssertionError("startup migration must only reuse stored evidence")

    monkeypatch.setattr(DeliverableMaterializer, "materialize", fail_side_effect)
    monkeypatch.setattr(
        DeliverableMaterializer,
        "read_managed_delivery",
        fail_side_effect,
    )
    monkeypatch.setattr(
        DeliverableMaterializer,
        "expected_filename",
        fail_side_effect,
    )
    monkeypatch.setattr(ArtifactService, "register_task_file_output", fail_side_effect)
    monkeypatch.setattr(ArtifactService, "register_task_output", fail_side_effect)
    monkeypatch.setattr(ArtifactService, "revalidate", fail_side_effect)
    monkeypatch.setattr(ArtifactService, "replace_current", fail_side_effect)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.SUCCEEDED
    assert migrated.final_output == stored_output
    assert migrated.completion_report is not None
    assert migrated.completion_report.artifact_ids == [artifact.id]
    assert all(
        result.status == CriterionResultStatus.PASSED
        for result in migrated.completion_report.criterion_results
    )
    assert all(
        result.evidence_artifact_ids == [artifact.id]
        for result in migrated.completion_report.criterion_results
    )
    assert [
        item.model_dump(mode="json")
        for item in migrated.artifacts
    ] == artifacts_before
    assert [
        item.model_dump(mode="json")
        for item in migrated.executions[0].artifacts
    ] == execution_artifacts_before
    assert {
        str(path.relative_to(output_root)): path.read_bytes()
        for path in output_root.rglob("*")
        if path.is_file()
    } == files_before


def test_task_service_migration_does_not_register_duplicate_text_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_text_evidence",
        include_requirement_evidence=True,
    )
    assert task.contract is not None
    task.contract = task.contract.model_copy(
        update={
            "deliverable_kind": "text",
            "deliverable_format": None,
            "deliverable_filename": "",
        },
        deep=True,
    )
    task.executions[0].contract_snapshot = task.contract.model_copy(deep=True)
    artifact = ArtifactService().register_task_output(task, task.final_output)
    assert artifact is not None
    assert task.completion_report is not None
    task.completion_report.artifact_ids = [artifact.id]
    task.executions[0].artifacts = [artifact.model_copy(deep=True)]
    task.executions[0].completion_report = task.completion_report.model_copy(deep=True)
    store.save(task)
    artifacts_before = [
        item.model_dump(mode="json")
        for item in task.artifacts
    ]

    monkeypatch.setattr(
        ArtifactService,
        "register_task_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("startup migration must not register task output")
        ),
    )

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.SUCCEEDED
    assert [
        item.model_dump(mode="json")
        for item in migrated.artifacts
    ] == artifacts_before
    assert migrated.completion_report is not None
    assert migrated.completion_report.artifact_ids == [artifact.id]


def test_task_service_migration_preserves_whitespace_output_but_treats_it_empty(
    tmp_path: Path,
) -> None:
    store = InMemoryTaskStore()
    stored_output = "  \n  "
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_whitespace_output",
        include_requirement_evidence=True,
        output=stored_output,
    )
    store.save(task)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.final_output == stored_output
    assert migrated.task_status == TaskStatus.RUNNING
    assert migrated.completion_report is not None
    assert migrated.completion_report.awaiting_human_decision is True
    assert "output is empty" in migrated.completion_report.evidence_summary


def test_task_service_migration_keeps_overflow_acceptance_content_visible(
    tmp_path: Path,
) -> None:
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_overflow_criteria",
        include_requirement_evidence=True,
    )
    now = utc_now()
    contract = TaskContract(
        goal="Prepare legacy delivery",
        deliverable_goal="Legacy reviewable output",
        deliverable_requirements=[
            TaskContractItem(
                id=f"requirement_{index}",
                description=f"Requirement {index}",
            )
            for index in range(1, 7)
        ],
        success_criteria=[
            TaskContractItem(
                id=f"criterion_{index}",
                description=f"Criterion {index}",
            )
            for index in range(1, 7)
        ],
        requires_human_acceptance=True,
        confirmed_at=now,
    )
    task.contract = contract
    task.executions[0].contract_snapshot = contract.model_copy(deep=True)
    assert task.completion_report is not None
    task.completion_report.criterion_results = [
        CriterionResult(
            criterion_id=item.id,
            status=CriterionResultStatus.PASSED,
            evidence_text=task.final_output,
        )
        for item in [
            *contract.deliverable_requirements,
            *contract.success_criteria,
        ]
    ]
    task.executions[0].completion_report = task.completion_report.model_copy(deep=True)
    store.save(task)

    service = TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.contract is not None
    assert len(migrated.contract.success_criteria) == 10
    aggregate = migrated.contract.success_criteria[-1]
    assert aggregate.description.startswith("同时满足以下历史验收标准：")
    assert all(
        description in aggregate.description
        for description in ("Criterion 4", "Criterion 5", "Criterion 6")
    )
    assert migrated.task_status == TaskStatus.RUNNING
    assert migrated.completion_report is not None
    assert migrated.completion_report.awaiting_human_decision is True
    assert aggregate.id in migrated.completion_report.evidence_summary

    accepted = service.submit_result(
        task.id,
        ExecutionResultCreate(
            result_status="succeeded",
            output="All visible acceptance criteria passed",
        ),
    )

    assert accepted.task_status == TaskStatus.SUCCEEDED
    assert accepted.completion_report is not None
    assert accepted.completion_report.awaiting_human_decision is False
    assert len(accepted.completion_report.criterion_results) == 10
    assert all(
        result.status == CriterionResultStatus.PASSED
        for result in accepted.completion_report.criterion_results
    )


def test_task_service_migration_waits_for_missing_promoted_criterion_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OUTPUT_DIR", str(tmp_path / "outputs"))
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_legacy_missing_evidence",
        include_requirement_evidence=False,
    )
    store.save(task)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.RUNNING
    assert migrated.current_node == CurrentNode.HUMAN_INTERVENTION
    assert migrated.completion_report is not None
    assert migrated.completion_report.awaiting_human_decision is True
    assert "requirement_summary" in migrated.completion_report.evidence_summary
    assert migrated.executions[0].finished_at is None
    assert any(event.type == "criteria_only_migrated" for event in migrated.events)


def test_task_service_does_not_rejudge_ordinary_human_node_without_report(
    tmp_path: Path,
) -> None:
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_ordinary_human_node",
        include_requirement_evidence=True,
    )
    task.completion_report = None
    task.executions[0].completion_report = None
    store.save(task)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.RUNNING
    assert migrated.current_node == CurrentNode.HUMAN_INTERVENTION
    assert migrated.completion_report is None
    assert migrated.executions[0].status == TaskStatus.RUNNING
    assert migrated.executions[0].finished_at is None


def test_task_service_does_not_rejudge_non_system_legacy_report(
    tmp_path: Path,
) -> None:
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_human_decided_legacy_report",
        include_requirement_evidence=True,
    )
    assert task.completion_report is not None
    task.completion_report.human_accepted = True
    task.completion_report.decided_by_type = "human"
    task.completion_report.decided_by_id = "root"
    task.executions[0].completion_report = task.completion_report.model_copy(
        deep=True
    )
    store.save(task)

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    migrated = store.get(task.id)
    assert migrated is not None
    assert migrated.task_status == TaskStatus.RUNNING
    assert migrated.current_node == CurrentNode.HUMAN_INTERVENTION
    assert migrated.completion_report is not None
    assert migrated.completion_report.awaiting_human_decision is False
    assert migrated.completion_report.decided_by_type == "human"
    assert migrated.completion_report.human_accepted is True
    assert migrated.executions[0].finished_at is None


def test_task_service_does_not_migrate_matching_report_without_legacy_gate(
    tmp_path: Path,
) -> None:
    store = InMemoryTaskStore()
    task = _legacy_pending_acceptance_task(
        task_id="task_matching_report_without_legacy_gate",
        include_requirement_evidence=True,
    )
    assert task.contract is not None
    task.contract = TaskContractService().normalize_contract(task.contract)
    task.executions[0].contract_snapshot = task.contract.model_copy(deep=True)
    store.save(task)
    before = task.model_dump(mode="json")

    TaskService(store, AgentRegistry(tmp_path / "agents.json"))

    unchanged = store.get(task.id)
    assert unchanged is not None
    assert unchanged.model_dump(mode="json") == before
    assert not any(
        event.type == "criteria_only_migrated" for event in unchanged.events
    )


def test_legacy_task_defaults_execution_history_without_losing_initial_context() -> None:
    legacy_task = Task.model_validate(
        {
            "id": "task_legacy",
            "source_type": "business_system",
            "content": "legacy request",
            "task_status": "running",
            "current_node": "human_confirmation",
            "context": {"summary": "legacy context", "artifacts": ["legacy.txt"]},
            "created_at": "2026-07-20T00:00:00Z",
            "updated_at": "2026-07-20T00:00:00Z",
        }
    )

    assert legacy_task.initial_context == legacy_task.context
    assert legacy_task.initial_context is not legacy_task.context
    assert legacy_task.executions == []
    assert legacy_task.active_execution_id is None
    assert legacy_task.completion_report is None


def test_task_request_title_must_not_exceed_50_chars(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "超" * 51,
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 422


def test_task_request_uses_manual_task_name_for_task_title(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户交付报告任务",
            "content": "分析客户需求并生成交付报告",
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"][0]
    assert task["title"] == "客户交付报告任务"
    assert task["draft"]["title"] == "分析客户需求并生成交付报告"


def test_confirm_task_replaces_title_and_description_with_confirmed_values(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "原始任务名",
            "content": "原始任务诉求",
        },
    ).json()["tasks"][0]

    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "确认后的任务名",
            "description": "确认后的任务描述，包含人工审核要求",
        },
    ).json()

    assert confirmed["title"] == "确认后的任务名"
    assert confirmed["description"] == "确认后的任务描述，包含人工审核要求"


def test_first_confirmation_creates_exactly_one_initial_execution(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    monkeypatch.setattr(
        "app.services.task_service.TaskService.start_background_task",
        lambda self, task_id, expected_execution_id=None: None,
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery plan"},
    ).json()["tasks"][0]

    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Prepare delivery plan",
            "description": "Prepare an auditable delivery plan",
            "execution_mode": "async",
        },
    ).json()

    assert len(confirmed["executions"]) == 1
    execution = confirmed["executions"][0]
    assert confirmed["active_execution_id"] == execution["id"]
    assert execution["task_id"] == created["id"]
    assert execution["attempt_no"] == 1
    assert execution["trigger_type"] == "initial"
    assert execution["trigger_reason"]
    assert execution["triggered_by_user_id"] == "root"
    assert execution["triggered_by_user_name"] == "管理员"
    assert execution["execution_mode"] == "async"
    assert execution["contract_snapshot"] == confirmed["contract"]
    assert execution["workflow_snapshot"] is None
    assert execution["status"] == confirmed["task_status"]
    assert execution["start_node"] == "dispatch_decision"
    assert execution["current_node"] == confirmed["current_node"]
    assert execution["context_snapshot"] == confirmed["context"]
    assert execution["loop_count"] == confirmed["loop_count"]
    assert execution["final_output"] == confirmed["final_output"]
    assert execution["created_at"]
    assert execution["started_at"] is None
    assert execution["finished_at"] is None
    assert execution["parent_execution_id"] is None
    assert execution["retry_of_execution_id"] is None
    assert execution["completion_report"] is None


def test_confirm_task_records_structured_contract_from_current_user(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    user = client.post(
        "/api/v1/users",
        json={"name": "张三", "phone": "13800000001", "email": "alice@example.com", "role": "user"},
    ).json()
    headers = {"X-User-Id": user["id"]}
    created = client.post(
        "/api/v1/tasks/requests",
        headers=headers,
        json={
            "source_type": "business_system",
            "title": "客户方案任务",
            "content": "为客户生成可以验收的实施方案",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        headers=headers,
        json={
            "title": "客户方案任务",
            "description": "为客户生成可以验收的实施方案",
            "contract": {
                "goal": "形成客户认可的实施路径",
                "deliverable_goal": "交付一份实施方案文档",
                "deliverable_kind": "file",
                "deliverable_format": "markdown",
                "deliverable_filename": "  implementation-plan.md  ",
                "deliverable_requirements": [
                    {"id": "requirement_markdown", "description": "Markdown 格式"},
                    {"description": "包含风险与里程碑"},
                ],
                "success_criteria": [
                    {"id": "criterion_reviewable", "description": "内容可以直接进入评审"},
                    {"description": "风险均有应对措施"},
                ],
                "requires_human_acceptance": True,
            },
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    confirmed = response.json()
    contract = confirmed["contract"]
    assert contract["goal"] == "形成客户认可的实施路径"
    assert contract["deliverable_goal"] == "交付一份实施方案文档"
    assert contract["deliverable_kind"] == "text"
    assert contract["deliverable_format"] is None
    assert contract["deliverable_filename"] == ""
    assert contract["version"] == 2
    assert contract["deliverable_requirements"] == []
    assert contract["success_criteria"][0] == {
        "id": "requirement_markdown",
        "description": "Markdown 格式",
    }
    assert contract["success_criteria"][1]["id"]
    assert contract["success_criteria"][2]["id"] == "criterion_reviewable"
    assert contract["success_criteria"][3]["id"]
    assert contract["requires_human_acceptance"] is False
    assert contract["confirmed_by_user_id"] == user["id"]
    assert contract["confirmed_by_user_name"] == "张三"
    assert contract["confirmed_at"]
    assert contract["legacy_inferred"] is False
    assert confirmed["executions"][0]["triggered_by_user_id"] == user["id"]
    assert confirmed["executions"][0]["triggered_by_user_name"] == "张三"
    assert confirmed["executions"][0]["contract_snapshot"] == contract


def test_confirm_task_normalizes_legacy_delivery_fields_into_visible_criteria(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    monkeypatch.setattr(
        client.app.state.task_service,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare a reviewable report"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Reviewable report",
            "description": "Prepare a reviewable report",
            "contract": {
                "goal": "Produce an auditable result",
                "deliverable_goal": "Deliver the report",
                "deliverable_kind": "file",
                "deliverable_format": "text",
                "deliverable_filename": "report.patch",
                "deliverable_requirements": [
                    {"description": f"Legacy requirement {index}"}
                    for index in range(1, 7)
                ],
                "success_criteria": [
                    {"description": "Legacy requirement 1"},
                    *[
                        {"description": f"Visible criterion {index}"}
                        for index in range(1, 7)
                    ],
                ],
                "requires_human_acceptance": True,
            },
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    contract = response.json()["contract"]
    persisted_contract = client.app.state.task_service.get_task(created["id"]).contract
    assert persisted_contract is not None
    assert contract == persisted_contract.model_dump(mode="json")
    assert contract["deliverable_kind"] == "text"
    assert contract["deliverable_format"] is None
    assert contract["deliverable_filename"] == ""
    assert contract["deliverable_requirements"] == []
    descriptions = [item["description"] for item in contract["success_criteria"]]
    assert descriptions[:9] == [
        *[f"Legacy requirement {index}" for index in range(1, 7)],
        *[f"Visible criterion {index}" for index in range(1, 4)],
    ]
    assert descriptions[9].startswith("同时满足以下历史验收标准：")
    assert all(
        description in descriptions[9]
        for description in (
            "Visible criterion 4",
            "Visible criterion 5",
            "Visible criterion 6",
        )
    )
    assert contract["requires_human_acceptance"] is False
    assert response.json()["executions"][0]["contract_snapshot"] == contract


def test_confirmed_task_waiting_for_dependencies_uses_initial_execution(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    service = client.app.state.task_service
    prerequisite = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare prerequisite"},
    ).json()["tasks"][0]
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Wait for prerequisite"},
    ).json()["tasks"][0]
    task = service.get_task(created["id"])
    task.dependency_task_ids = [prerequisite["id"]]
    service.store.save(task)

    confirmed = service.confirm_task_details(
        task.id,
        TaskConfirm(title="Dependent task", description="Wait for prerequisite"),
        confirmed_by=client.app.state.user_registry.get_user("root"),
    )

    assert confirmed.current_node == CurrentNode.WAITING_DEPENDENCIES
    assert len(confirmed.executions) == 1
    assert confirmed.active_execution_id == confirmed.executions[0].id
    assert confirmed.executions[0].start_node == CurrentNode.WAITING_DEPENDENCIES
    assert confirmed.executions[0].current_node == CurrentNode.WAITING_DEPENDENCIES
    assert confirmed.executions[0].started_at is None

    prerequisite_task = service.get_task(prerequisite["id"])
    prerequisite_task.task_status = TaskStatus.SUCCEEDED
    prerequisite_task.current_node = CurrentNode.COMPLETION_JUDGE
    prerequisite_task.final_output = "prerequisite ready"
    service.store.save(prerequisite_task)
    resumed = service.run_confirmed_task(confirmed.id)

    assert resumed.executions[0].started_at is not None


@pytest.mark.parametrize(
    "contract",
    [
        {
            "goal": "形成实施路径",
            "deliverable_goal": "交付实施方案",
            "success_criteria": [{"description": "可以评审"}],
            "confirmed_by_user_id": "spoofed-user",
        },
        {
            "goal": "形成实施路径",
            "deliverable_goal": "交付实施方案",
            "success_criteria": [{"description": "可以评审", "result": "passed"}],
        },
    ],
)
def test_confirm_task_rejects_unknown_contract_fields(tmp_path: Path, contract: dict) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "实施方案", "description": "生成实施方案", "contract": contract},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "contract",
    [
        {
            "goal": "形成实施路径",
            "deliverable_goal": "交付实施方案",
            "deliverable_requirements": [
                {"id": "requirement_duplicate", "description": "Markdown 格式"},
                {"id": "requirement_duplicate", "description": "包含里程碑"},
            ],
            "success_criteria": [{"description": "可以评审"}],
        },
        {
            "goal": "形成实施路径",
            "deliverable_goal": "交付实施方案",
            "success_criteria": [
                {"id": "criterion_duplicate", "description": "可以评审"},
                {"id": "criterion_duplicate", "description": "风险完整"},
            ],
        },
    ],
)
def test_confirm_task_rejects_duplicate_client_item_ids(tmp_path: Path, contract: dict) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "实施方案", "description": "生成实施方案", "contract": contract},
    )

    assert response.status_code == 422


def test_confirm_task_accepts_empty_deliverable_requirements(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "实施方案",
            "description": "生成实施方案",
            "contract": {
                "goal": "形成实施路径",
                "deliverable_goal": "交付实施方案",
                "deliverable_requirements": [],
                "success_criteria": [{"description": "可以评审"}],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["contract"]["deliverable_requirements"] == []


def test_task_contract_rejects_more_than_ten_acceptance_criteria() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(
                success_criteria=[
                    {"description": f"验收标准 {index}"}
                    for index in range(1, 12)
                ]
            )
        )

    assert "List should have at most 10 items" in str(exc_info.value)


def test_confirm_task_without_contract_records_legacy_inferred_contract(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "旧客户端任务",
            "content": "生成旧客户端仍可读取的结果",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "旧客户端任务", "description": "生成旧客户端仍可读取的结果"},
    )

    assert response.status_code == 200
    contract = response.json()["contract"]
    assert contract["goal"] == "生成旧客户端仍可读取的结果"
    assert contract["deliverable_goal"] == "旧客户端任务"
    assert len(contract["success_criteria"]) >= 1
    assert contract["confirmed_by_user_id"] == "root"
    assert contract["legacy_inferred"] is True


def test_legacy_contract_ignores_model_suggestions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda content, agents: [
            {
                "title": "模型建议标题",
                "description": "模型建议描述",
                "confidence": 0.9,
                "goal": "模型建议目标",
                "deliverable_goal": "模型建议交付物",
                "deliverable_requirements": ["模型建议格式"],
                "success_criteria": ["模型建议标准"],
                "requires_human_acceptance": True,
            }
        ],
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "原始请求"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "人工确认标题", "description": "人工确认描述"},
    )

    assert response.status_code == 200
    contract = response.json()["contract"]
    assert contract["goal"] == "人工确认描述"
    assert contract["deliverable_goal"] == "人工确认标题"
    assert contract["deliverable_requirements"] == []
    assert contract["success_criteria"][0]["description"] == "已产生与确认目标“人工确认描述”一致的可审核结果"
    assert contract["requires_human_acceptance"] is False
    assert contract["legacy_inferred"] is True


def test_confirmed_contract_cannot_be_overwritten(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]
    first = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "首次确认标题",
            "description": "首次确认描述",
            "contract": {
                "goal": "首次目标",
                "deliverable_goal": "首次交付物",
                "success_criteria": [{"id": "criterion_original", "description": "首次标准"}],
            },
        },
    ).json()

    second = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "覆盖标题",
            "description": "覆盖描述",
            "contract": {
                "goal": "覆盖目标",
                "deliverable_goal": "覆盖交付物",
                "success_criteria": [{"id": "criterion_replaced", "description": "覆盖标准"}],
            },
        },
    )

    assert second.status_code == 409
    reloaded = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert reloaded["contract"] == first["contract"]
    assert reloaded["title"] == "首次确认标题"
    assert reloaded["description"] == "首次确认描述"


def test_concurrent_confirmation_creates_one_execution_and_returns_one_conflict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    service = client.app.state.task_service
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "并发确认任务"},
    ).json()["tasks"][0]
    monkeypatch.setattr(
        "app.services.task_service.TaskService.start_background_task",
        lambda self, task_id, expected_execution_id=None: None,
    )
    original_confirm = service.task_contract_service.confirm_contract
    second_call_entered = Event()
    call_count_lock = Lock()
    call_count = 0

    def delayed_confirm(task, payload, actor):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            second_call_entered.wait(timeout=0.3)
        else:
            second_call_entered.set()
        return original_confirm(task, payload, actor)

    monkeypatch.setattr(service.task_contract_service, "confirm_contract", delayed_confirm)
    payload = {
        "title": "并发确认任务",
        "description": "同一任务只能确认一次",
        "execution_mode": "async",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: client.post(f"/api/v1/tasks/{created['id']}/confirm", json=payload),
                range(2),
            )
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    reloaded = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert len(reloaded["executions"]) == 1


def test_legacy_task_outside_human_confirmation_cannot_be_confirmed(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]
    service = client.app.state.task_service
    legacy_task = service.get_task(created["id"])
    legacy_task.current_node = CurrentNode.COMPLETION_JUDGE
    legacy_task.task_status = TaskStatus.SUCCEEDED
    legacy_task.final_output = "历史执行结果"
    service.store.save(legacy_task)
    before = client.get(f"/api/v1/tasks/{created['id']}").json()

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "试图覆盖历史任务",
            "description": "不应重新确认",
            "contract": {
                "goal": "覆盖目标",
                "deliverable_goal": "覆盖交付物",
                "success_criteria": [{"description": "覆盖标准"}],
            },
        },
    )

    assert response.status_code == 409
    assert client.get(f"/api/v1/tasks/{created['id']}").json() == before


def test_task_service_confirmation_requires_explicit_actor(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]

    with pytest.raises(TypeError):
        client.app.state.task_service.confirm_task_details(
            created["id"],
            TaskConfirm(title="实施方案", description="生成实施方案"),
        )


@pytest.mark.parametrize(
    "contract",
    [
        {
            "goal": "   ",
            "deliverable_goal": "交付方案",
            "success_criteria": [{"description": "可以评审"}],
        },
        {
            "goal": "完成方案",
            "deliverable_goal": "   ",
            "success_criteria": [{"description": "可以评审"}],
        },
        {
            "goal": "完成方案",
            "deliverable_goal": "交付方案",
            "success_criteria": [],
        },
        {
            "goal": "完成方案",
            "deliverable_goal": "交付方案",
            "success_criteria": [{"description": "   "}],
        },
    ],
)
def test_confirm_task_rejects_invalid_contract(tmp_path: Path, contract: dict) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "生成实施方案"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "实施方案", "description": "生成实施方案", "contract": contract},
    )

    assert response.status_code == 422


def _contract_input(**overrides) -> dict:
    payload = {
        "goal": "形成实施路径",
        "deliverable_goal": "交付实施方案",
        "success_criteria": [{"description": "可以评审"}],
    }
    payload.update(overrides)
    return payload


def test_file_contract_requires_deliverable_format() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(deliverable_kind="file", deliverable_filename="plan.md")
        )

    assert "deliverable_format is required for file deliverables" in str(exc_info.value)


@pytest.mark.parametrize(
    "filename",
    [
        ".",
        "..",
        "../plan.md",
        "folder/plan.md",
        "folder\\plan.md",
        "D:plan.md",
        "CON.md",
        "con.MD",
        "LPT9.md",
        "plan?.md",
        "plan|draft.md",
        "plan\x00.md",
        "plan\x1f.md",
        "plan\x7f.md",
        "plan\x85.md",
        "plan.md.",
    ],
)
def test_file_contract_rejects_non_plain_filename(filename: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(
                deliverable_kind="file",
                deliverable_format="markdown",
                deliverable_filename=filename,
            )
        )

    assert "deliverable_filename must be a plain filename" in str(exc_info.value)


@pytest.mark.parametrize(
    "filename",
    ["a" * 253, "文" * 85],
)
def test_file_contract_rejects_filename_over_255_utf8_bytes_after_extension(
    filename: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(
                deliverable_kind="file",
                deliverable_format="markdown",
                deliverable_filename=filename,
            )
        )

    assert "deliverable_filename must be at most 255 UTF-8 bytes" in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    ("deliverable_format", "filename"),
    [
        ("markdown", "CONIN$.md"),
        ("text", "conout$.txt"),
        ("markdown", "COM¹.md"),
        ("text", "com².txt"),
        ("markdown", "Com³.md"),
        ("text", "LPT¹.txt"),
        ("markdown", "lpt².md"),
        ("text", "LpT³.txt"),
    ],
)
def test_file_contract_rejects_additional_windows_device_names(
    deliverable_format: str,
    filename: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(
                deliverable_kind="file",
                deliverable_format=deliverable_format,
                deliverable_filename=filename,
            )
        )

    assert "deliverable_filename must be a plain filename" in str(exc_info.value)


@pytest.mark.parametrize(
    ("deliverable_format", "filename", "expected_extension"),
    [
        ("markdown", "plan.txt", ".md"),
        ("text", "plan.md", ".txt"),
        ("text", "plan.pdf", ".txt"),
    ],
)
def test_file_contract_rejects_wrong_extension(
    deliverable_format: str,
    filename: str,
    expected_extension: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(
            _contract_input(
                deliverable_kind="file",
                deliverable_format=deliverable_format,
                deliverable_filename=filename,
            )
        )

    assert f"deliverable_filename extension must be {expected_extension}" in str(exc_info.value)


@pytest.mark.parametrize(
    ("filename", "expected_filename"),
    [
        ("", ""),
        ("   ", ""),
        ("delivery", "delivery"),
        ("  PLAN.MD  ", "PLAN.MD"),
        ("文" * 84, "文" * 84),
    ],
)
def test_file_contract_accepts_empty_extensionless_and_matching_filename(
    filename: str,
    expected_filename: str,
) -> None:
    contract = TaskContractInput.model_validate(
        _contract_input(
            deliverable_kind="file",
            deliverable_format="markdown",
            deliverable_filename=filename,
        )
    )

    assert contract.deliverable_filename == expected_filename


@pytest.mark.parametrize(
    "deliverable_fields",
    [
        {"deliverable_format": "markdown"},
        {"deliverable_filename": "plan"},
    ],
)
def test_text_contract_rejects_file_delivery_fields(deliverable_fields: dict) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TaskContractInput.model_validate(_contract_input(**deliverable_fields))

    assert "text deliverables cannot define file delivery fields" in str(exc_info.value)


def test_legacy_contract_and_draft_default_to_text_deliverable() -> None:
    contract = TaskContract.model_validate(
        {
            "goal": "Prepare delivery",
            "deliverable_goal": "Reviewable delivery",
            "success_criteria": [{"description": "Reviewable"}],
            "confirmed_at": "2026-07-20T00:00:00Z",
        }
    )
    draft = TaskDraft.model_validate(
        {
            "title": "Prepare delivery",
            "description": "Prepare a reviewable delivery",
            "confidence": 0.8,
        }
    )

    for item in (contract, draft):
        assert item.deliverable_kind == "text"
        assert item.deliverable_format is None
        assert item.deliverable_filename == ""


def test_task_confirm_normalizes_task_contract_instance_without_extra_fields() -> None:
    confirmed_contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        deliverable_kind="file",
        deliverable_format="markdown",
        deliverable_filename="delivery.md",
        success_criteria=[TaskContractItem(description="Reviewable")],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )

    payload = TaskConfirm(
        title="Prepare delivery",
        description="Prepare a reviewable delivery",
        contract=confirmed_contract,
    )

    assert type(payload.contract) is TaskContractInput
    assert payload.contract.deliverable_kind == "text"
    assert payload.contract.deliverable_format is None
    assert payload.contract.deliverable_filename == ""
    assert payload.contract.requires_human_acceptance is False


def test_merge_drafts_normalizes_single_legacy_draft_to_visible_criteria(
    tmp_path: Path,
) -> None:
    service = TestClient(create_app(agent_file=tmp_path / "agents.json")).app.state.task_service

    merged = service._merge_drafts(
        "Prepare delivery",
        [
            {
                "draft_key": "draft_plan",
                "title": "Write plan",
                "description": "Write the plan",
                "confidence": 0.8,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": "agent_writer",
                "depends_on": ["draft_analysis"],
                "goal": "Agree on the plan",
                "deliverable_goal": "Deliver a reviewable plan",
                "deliverable_kind": "file",
                "deliverable_format": "text",
                "deliverable_filename": "report.patch",
                "deliverable_requirements": ["Legacy requirement", "Shared criterion"],
                "success_criteria": ["Shared criterion", "Visible criterion"],
                "requires_human_acceptance": True,
            }
        ],
    )

    assert merged.draft_key == "draft_plan"
    assert merged.suggested_assignee_type == "agent"
    assert merged.suggested_agent_id == "agent_writer"
    assert merged.depends_on == ["draft_analysis"]
    assert merged.goal == "Agree on the plan"
    assert merged.deliverable_goal == "Deliver a reviewable plan"
    assert merged.deliverable_kind == "text"
    assert merged.deliverable_format is None
    assert merged.deliverable_filename == ""
    assert merged.deliverable_requirements == []
    assert merged.success_criteria == [
        "Legacy requirement",
        "Shared criterion",
        "Visible criterion",
    ]
    assert merged.requires_human_acceptance is False


def test_merge_drafts_normalizes_multiple_legacy_drafts_to_visible_criteria(
    tmp_path: Path,
) -> None:
    service = TestClient(create_app(agent_file=tmp_path / "agents.json")).app.state.task_service

    merged = service._merge_drafts(
        "Prepare delivery",
        [
            {
                "title": "Analyze",
                "description": "Analyze requirements",
                "confidence": 0.9,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": "agent_analyst",
                "deliverable_goal": "Deliver analysis",
                "deliverable_requirements": ["Legacy requirement 1", "Shared criterion"],
                "success_criteria": ["Visible criterion 1"],
                "requires_human_acceptance": True,
            },
            {
                "title": "Write plan",
                "description": "Write the plan",
                "confidence": 0.8,
                "deliverable_kind": "file",
                "deliverable_format": "text",
                "deliverable_filename": "report.patch",
                "deliverable_goal": "Deliver plan",
                "deliverable_requirements": ["Legacy requirement 2"],
                "success_criteria": ["Shared criterion", "Visible criterion 2"],
            },
        ],
    )

    assert merged.suggested_assignee_type == "agent"
    assert merged.suggested_agent_id == "agent_analyst"
    assert merged.goal == "Prepare delivery"
    assert merged.deliverable_goal == "Deliver analysis; Deliver plan"
    assert merged.deliverable_kind == "text"
    assert merged.deliverable_format is None
    assert merged.deliverable_filename == ""
    assert merged.deliverable_requirements == []
    assert merged.success_criteria == [
        "Legacy requirement 1",
        "Shared criterion",
        "Legacy requirement 2",
        "Visible criterion 1",
        "Visible criterion 2",
    ]
    assert merged.requires_human_acceptance is False


def test_merge_drafts_defaults_to_text_without_explicit_file(tmp_path: Path) -> None:
    service = TestClient(create_app(agent_file=tmp_path / "agents.json")).app.state.task_service

    merged = service._merge_drafts(
        "Prepare delivery",
        [
            {
                "title": "Markdown report",
                "description": "Create a markdown file",
                "confidence": 0.9,
            },
            {
                "title": "Plain text notes",
                "description": "Create a text document",
                "confidence": 0.8,
            },
        ],
    )

    assert merged.deliverable_kind == "text"
    assert merged.deliverable_format is None
    assert merged.deliverable_filename == ""


def test_task_list_and_detail_are_scoped_to_normal_user(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    alice = client.post(
        "/api/v1/users",
        json={"name": "张三", "phone": "13800000001", "email": "alice@example.com", "role": "user"},
    ).json()
    bob = client.post(
        "/api/v1/users",
        json={"name": "李四", "phone": "13800000002", "email": "bob@example.com", "role": "user"},
    ).json()

    alice_task = client.post(
        "/api/v1/tasks/requests",
        headers={"X-User-Id": alice["id"]},
        json={
            "source_type": "business_system",
            "title": "张三发起任务",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]
    bob_task = client.post(
        "/api/v1/tasks/requests",
        headers={"X-User-Id": bob["id"]},
        json={
            "source_type": "business_system",
            "title": "李四发起任务",
            "content": "Review contract for customer B",
        },
    ).json()["tasks"][0]

    assert alice_task["created_by_user_id"] == alice["id"]
    assert alice_task["created_by_user_name"] == "张三"
    assert bob_task["created_by_user_id"] == bob["id"]

    admin_task_ids = {task["id"] for task in client.get("/api/v1/tasks").json()}
    assert {alice_task["id"], bob_task["id"]}.issubset(admin_task_ids)

    alice_task_ids = {task["id"] for task in client.get("/api/v1/tasks", headers={"X-User-Id": alice["id"]}).json()}
    assert alice_task_ids == {alice_task["id"]}
    assert client.get(f"/api/v1/tasks/{alice_task['id']}", headers={"X-User-Id": alice["id"]}).status_code == 200
    assert client.get(f"/api/v1/tasks/{bob_task['id']}", headers={"X-User-Id": alice["id"]}).status_code == 403


def test_unconfirmed_task_can_be_soft_cancelled_and_retained(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer B",
        },
    ).json()["tasks"][0]

    response = client.delete(f"/api/v1/tasks/{created['id']}")

    assert response.status_code == 204
    cancelled = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert cancelled["task_status"] == "cancelled"
    assert cancelled["completion_report"]["terminal_status"] == "cancelled"
    assert cancelled["completion_report"]["completion_reason"] == "Cancelled before confirmation"
    assert cancelled["completion_report"]["execution_id"] == ""
    assert cancelled["completion_report"]["decided_by_type"] == "human"
    assert cancelled["completion_report"]["decided_by_id"] == "root"
    assert cancelled["events"][-1]["type"] == "task_cancelled"
    assert [task["id"] for task in client.get("/api/v1/tasks").json()] == [created["id"]]


def test_no_workflow_human_subtask_defaults_to_root_assignee(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="人工审核",
                    description="需要人工审核当前任务",
                    assignee_type="human",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "人工审核任务",
            "content": "请人工审核方案",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "人工审核任务", "description": "请人工审核方案"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == "root"
    assert human_subtask["assignee_user_name"] == "管理员"
    assert human_subtask["assignee_role"] == "admin"

    assert client.get("/api/v1/subtasks/human?assignee_user_id=root").json()[0]["id"] == human_subtask["id"]
    assert client.get("/api/v1/subtasks/human?assignee_user_id=user_001").json() == []


def test_no_workflow_human_subtask_infers_registered_assignee(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    user = client.post("/api/v1/users", json={"name": "王大锤"}).json()

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="王大锤确认方案",
                    description="需要王大锤确认当前方案是否可行",
                    assignee_type="human",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户B交付方案评审",
            "content": "整理方案后需要王大锤确认",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户B交付方案评审", "description": "整理方案后需要王大锤确认"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == user["id"]
    assert human_subtask["assignee_user_name"] == "王大锤"
    assert human_subtask["assignee_role"] == "approver"
    assert client.get(f"/api/v1/subtasks/human?assignee_user_id={user['id']}").json()[0]["id"] == human_subtask["id"]


def test_no_workflow_human_subtask_ignores_missing_model_assignee_and_uses_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.models import RoundPlan, SubTask, new_id

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, _agents):
        if task.loop_count > 0:
            return RoundPlan(should_continue=False, reason="done")
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason="needs human approval",
            subtasks=[
                SubTask(
                    id=new_id("subtask"),
                    title="人工确认方案",
                    description="请不存在的人确认当前方案",
                    assignee_type="human",
                    assignee_user_id="missing_user",
                    assignee_user_name="不存在的人",
                )
            ],
        )

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "客户B交付方案评审",
            "content": "整理方案后需要不存在的人确认",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户B交付方案评审", "description": "整理方案后需要不存在的人确认"},
    ).json()

    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == "root"
    assert human_subtask["assignee_user_name"] == "管理员"


def test_agent_subtask_failure_stops_whole_task(tmp_path: Path, monkeypatch) -> None:
    from app.core.models import RoundPlan, SubTask

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Requirement Agent",
            "description": "Handles requirement query",
            "capabilities": ["requirements"],
        },
    ).json()

    def _plan(task, _agents):
        if task.loop_count > 0:
            raise AssertionError("planner must not run again after a failed subtask")
        return RoundPlan(
            should_continue=True,
            execution_mode="parallel",
            reason="collect prerequisite information",
            subtasks=[
                SubTask(
                    id="subtask_failure_stop",
                    title="查询客户需求",
                    description="查询客户需求和预算",
                    assigned_agent_id=agent["id"],
                )
            ],
        )

    def _execute(task, subtask, agent, tool_results):
        return [], ""

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "查询客户需求后生成方案"},
    ).json()["tasks"][0]
    result = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "客户方案", "description": "查询客户需求后生成方案"},
    ).json()

    assert result["task_status"] == "failed"
    assert result["current_node"] == "completion_judge"
    assert result["completion_report"]["terminal_status"] == "failed"
    assert result["loop_count"] == 1
    assert len(result["context"]["rounds"]) == 1
    failed_subtask = result["context"]["rounds"][0]["subtasks"][0]
    assert failed_subtask["status"] == "failed"
    assert failed_subtask["output"] == "Agent returned no output"
    assert "查询客户需求: Agent returned no output" in result["final_output"]
    assert [event["type"] for event in result["events"]][-2:] == ["subtask_failed", "task_failed"]


def test_task_request_does_not_use_intent_mock_when_system_fallback_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"), raise_server_exceptions=False)

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 500


def test_task_request_intent_recognition_uses_registered_agents(tmp_path: Path, monkeypatch) -> None:
    captured_agents = []

    def _recognize(content, agents):
        captured_agents.extend(agents)
        return [
            {
                "title": "Create quote for customer A",
                "description": "Prepare quote for customer A",
                "confidence": 0.91,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
            }
        ]

    monkeypatch.setattr("app.services.task_service.recognize_tasks_with_model", _recognize)
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote tasks",
            "capabilities": ["quote"],
        },
    ).json()
    client.post(
        "/api/v1/agents",
        json={
            "name": "Canvas Human Node",
            "description": "Only used by workflow canvas",
            "agent_type": "human",
            "capabilities": ["approval"],
        },
    )

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"][0]
    assert [item.id for item in captured_agents] == [agent["id"]]
    assert task["draft"]["suggested_assignee_type"] == "agent"
    assert task["draft"]["suggested_agent_id"] == agent["id"]
    assert task["assigned_agent_id"] == agent["id"]


def test_background_task_failure_is_persisted_on_task(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "后台失败任务",
            "content": "Run a task that fails in background",
        },
    ).json()["tasks"][0]
    service = client.app.state.task_service
    service.confirm_task_details(
        created["id"],
        payload=TaskConfirm(
            title="后台失败任务",
            description="Run a task that fails in background",
        ),
        confirmed_by=client.app.state.user_registry.get_user("root"),
    )

    def _raise(_task):
        raise RuntimeError("model execution failed")

    monkeypatch.setattr(service, "_run_automatic_flow", _raise)

    result = service.run_confirmed_task(created["id"])

    assert result.task_status == "failed"
    assert result.current_node == "completion_judge"
    assert result.final_output == "model execution failed"
    assert result.events[-1].type == "task_failed"
    assert service.get_task(created["id"]).task_status == "failed"


def test_run_confirmed_task_preserves_working_task_after_followup_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.enums import ArtifactKind, ArtifactSourceType
    from app.core.model_client import AgentModelExecutionError
    from app.core.models import RoundPlan, SubTask, ToolCall

    output_path = tmp_path / "outputs" / "working-draft.md"
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Persistence Agent",
            "description": "Writes delivery drafts",
            "capabilities": ["delivery"],
            "tools": [
                {
                    "name": "write_delivery",
                    "type": "file_write",
                    "config": {"base_dir": str(output_path.parent)},
                }
            ],
        },
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Write a delivery draft and continue processing",
        },
    ).json()["tasks"][0]
    service = client.app.state.task_service
    service.confirm_task_details(
        created["id"],
        payload=TaskConfirm(
            title="Persist working delivery",
            description="Write a delivery draft and continue processing",
        ),
        confirmed_by=client.app.state.user_registry.get_user("root"),
    )
    monkeypatch.setenv("ENABLE_SYSTEM_MOCK_FALLBACK", "false")

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Write the delivery draft",
                subtasks=[
                    SubTask(
                        id="persist_draft",
                        title="Persist draft",
                        description="Write the draft before follow-up processing",
                        assigned_agent_id=agent["id"],
                    )
                ],
            )
        raise AssertionError("planner must not run again after follow-up failure")

    def _execute(_task, _subtask, _agent, tool_results):
        if not tool_results:
            return [
                ToolCall(
                    tool_name="write_delivery",
                    arguments={
                        "filename": "working-draft.md",
                        "content": "persisted draft body",
                    },
                )
            ], ""
        raise AgentModelExecutionError(attempts=2, last_error="followup model failure")

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    returned = service.run_confirmed_task(created["id"])
    persisted = service.get_task(created["id"])

    assert returned == persisted
    assert persisted.task_status == TaskStatus.FAILED
    assert len(persisted.context.rounds) == 1
    failed_subtask = persisted.context.rounds[0].subtasks[0]
    assert failed_subtask.status == TaskStatus.FAILED
    assert failed_subtask.output.startswith("Agent model execution failed during followup execution")
    assert len(failed_subtask.tool_results) == 1
    assert failed_subtask.tool_results[0].result == str(output_path.resolve())
    assert output_path.read_text(encoding="utf-8") == "persisted draft body"
    file_artifact = next(
        artifact
        for artifact in persisted.artifacts
        if artifact.kind == ArtifactKind.FILE
        and artifact.source_type == ArtifactSourceType.TOOL_RESULT
    )
    assert file_artifact.uri == output_path.resolve().as_uri()
    assert persisted.executions[0].artifacts == persisted.artifacts


def test_task_request_merges_identified_steps_into_one_main_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda content, agents: [
            {
                "title": "Create quote for customer A",
                "description": "Prepare quote for customer A",
                "confidence": 0.91,
            },
            {
                "title": "Review contract for customer B",
                "description": "Review contract risk for customer B",
                "confidence": 0.88,
            },
        ],
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    response = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for A; review contract for B",
        },
    )

    assert response.status_code == 201
    result = response.json()
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["current_node"] == "human_confirmation"
    assert tasks[0]["task_status"] == "running"
    assert "Create quote for customer A" in tasks[0]["draft"]["title"]
    assert "Review contract for customer B" in tasks[0]["draft"]["title"]


def test_confirm_task_runs_automatic_flow_until_success(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and CRM tasks",
            "capabilities": ["quote", "crm"],
        },
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer A",
            "description": "Prepare and send quote for customer A",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "succeeded"
    assert task["current_node"] == "completion_judge"
    assert task["assigned_agent_id"] is not None
    assert task["loop_count"] == 1
    event_types = [event["type"] for event in task["events"]]
    assert event_types[:5] == [
        "task_created",
        "intent_recognized",
        "human_confirmed",
        "dispatch_decided",
        "agent_executed",
    ]
    assert "context_updated" in event_types
    assert event_types[-1] == "completion_judged"
    assert task["context"]["summary"]
    assert task["initial_context"] == created["context"]
    assert task["initial_context"] != task["context"]
    assert len(task["executions"]) == 1
    execution = task["executions"][0]
    assert task["active_execution_id"] == execution["id"]
    assert execution["status"] == task["task_status"]
    assert execution["start_node"] == "dispatch_decision"
    assert execution["current_node"] == task["current_node"]
    assert execution["context_snapshot"] == task["context"]
    assert execution["loop_count"] == task["loop_count"]
    assert execution["final_output"] == task["final_output"]
    assert execution["started_at"]
    assert execution["finished_at"]


def test_confirm_task_can_return_before_automatic_flow_when_async_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer A",
        },
    ).json()["tasks"][0]

    scheduled_task_ids = []

    def _capture_background_start(self, task_id, expected_execution_id=None):
        scheduled_task_ids.append(task_id)

    monkeypatch.setattr("app.services.task_service.TaskService.start_background_task", _capture_background_start)

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer A",
            "description": "Prepare and send quote for customer A",
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "running"
    assert task["current_node"] == "dispatch_decision"
    assert task["title"] == "Create a quote for customer A"
    assert task["description"] == "Prepare and send quote for customer A"
    assert scheduled_task_ids == [created["id"]]
    assert [event["type"] for event in task["events"]][-2:] == [
        "human_confirmed",
        "async_execution_scheduled",
    ]

    execution_id = task["active_execution_id"]
    completed = client.app.state.task_service.run_confirmed_task(created["id"])
    assert completed.active_execution_id == execution_id
    assert len(completed.executions) == 1
    assert completed.executions[0].start_node == CurrentNode.DISPATCH_DECISION
    assert completed.executions[0].started_at is not None


def test_initial_context_remains_fixed_across_multiple_saves(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    monkeypatch.setattr(
        "app.services.task_service.TaskService.start_background_task",
        lambda self, task_id, expected_execution_id=None: None,
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Preserve initial context"},
    ).json()["tasks"][0]
    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Preserve initial context",
            "description": "Preserve initial context",
            "execution_mode": "async",
        },
    ).json()
    original_initial_context = confirmed["initial_context"]
    service = client.app.state.task_service
    task = service.get_task(created["id"])

    task.context.summary = "first update"
    service.schedule_confirmed_task(task.id)
    task.context.summary = "second update"
    service.schedule_confirmed_task(task.id)

    reloaded = service.get_task(task.id)
    assert reloaded.initial_context.model_dump(mode="json") == original_initial_context
    assert reloaded.context.summary == "second update"


def test_agent_can_poll_tasks_assigned_to_it(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and CRM tasks",
            "capabilities": ["quote", "crm"],
        },
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Create a quote for customer C",
        },
    ).json()["tasks"][0]
    client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Create a quote for customer C",
            "description": "Prepare quote and update crm for customer C",
        },
    )

    response = client.post(f"/api/v1/agents/{agent['id']}/poll")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == created["id"]
    assert tasks[0]["assigned_agent_id"] == agent["id"]


def test_confirm_task_without_matching_agent_goes_to_human_and_finishes(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Review legal contract risk",
        },
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Review legal contract risk",
            "description": "No matching local agent should force human node",
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["task_status"] == "succeeded"
    assert task["current_node"] == "completion_judge"
    assert task["assigned_agent_id"] is None
    event_types = [event["type"] for event in task["events"]]
    assert "human_node_processed" in event_types


def test_multi_round_task_updates_context_before_next_subtask_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda content, agents: [
            {
                "draft_key": "collect_info",
                "title": "Collect customer info",
                "description": "Collect customer requirements",
                "confidence": 0.91,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
                "depends_on": [],
            },
            {
                "draft_key": "create_quote",
                "title": "Create quote",
                "description": "Create quote after requirements are ready",
                "confidence": 0.88,
                "suggested_assignee_type": "agent",
                "suggested_agent_id": agents[0].id,
                "depends_on": ["collect_info"],
            },
        ],
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote and customer requirements tasks",
            "capabilities": ["quote", "requirements"],
        },
    )
    seen_contexts = []

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask, new_id

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Collect prerequisite information first",
                subtasks=[
                    SubTask(
                        id=new_id("subtask"),
                        title="Collect customer info",
                        description="Collect customer requirements",
                        assigned_agent_id=agents[0].id,
                    )
                ],
            )
        if task.loop_count == 1 and "requirements ready" in task.context.summary:
            return RoundPlan(
                should_continue=True,
                reason="Use collected information to create quote",
                subtasks=[
                    SubTask(
                        id=new_id("subtask"),
                        title="Create quote",
                        description="Create quote after requirements are ready",
                        assigned_agent_id=agents[0].id,
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        seen_contexts.append(task.context.summary)
        if subtask.title == "Collect customer info":
            return [], "requirements ready"
        return [], f"quote created using context: {task.context.summary}"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created_task = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "content": "Collect requirements, then create a quote",
        },
    ).json()["tasks"][0]

    result = client.post(
        f"/api/v1/tasks/{created_task['id']}/confirm",
        json={
            "title": created_task["draft"]["title"],
            "description": created_task["draft"]["description"],
        },
    ).json()

    assert result["task_status"] == "succeeded"
    assert result["loop_count"] == 2
    assert len(result["context"]["rounds"]) == 2
    assert seen_contexts == ["", "requirements ready"]
    assert "quote created using context: requirements ready" in result["context"]["summary"]


def test_human_subtask_pauses_round_until_result_is_submitted(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "Quote Agent",
            "description": "Handles quote tasks",
            "capabilities": ["quote"],
        },
    ).json()

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need agent work and human approval",
                subtasks=[
                    SubTask(
                        id="subtask_agent_parallel",
                        title="Prepare quote",
                        description="Prepare quote draft",
                        assigned_agent_id=agent["id"],
                    ),
                    SubTask(
                        id="subtask_human_parallel",
                        title="Approve discount",
                        description="Human must approve the discount",
                        assignee_type="human",
                    ),
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _execute(task, subtask, agent, tool_results):
        return [], "agent quote draft ready"

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.workflows.task_graph.execute_subtask_with_tools_model", _execute)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare quote and approve discount"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Prepare quote", "description": "Prepare quote and approve discount"},
    ).json()

    assert paused["task_status"] == "running"
    assert paused["current_node"] == "human_execution"
    subtasks = paused["context"]["rounds"][0]["subtasks"]
    assert subtasks[0]["status"] == "succeeded"
    assert subtasks[0]["output"] == "agent quote draft ready"
    assert subtasks[1]["status"] == "running"
    assert subtasks[1]["assignee_type"] == "human"

    human_tasks = client.get("/api/v1/subtasks/human").json()
    assert len(human_tasks) == 1
    assert human_tasks[0]["id"] == subtasks[1]["id"]
    assert human_tasks[0]["logical_key"] == "subtask_human_parallel"
    assert human_tasks[0]["task_id"] == created["id"]
    assert human_tasks[0]["task_title"] == "Prepare quote"
    assert human_tasks[0]["task_description"] == "Prepare quote and approve discount"
    assert human_tasks[0]["task_content"] == "Prepare quote and approve discount"
    assert human_tasks[0]["task_context_summary"] == ""
    assert human_tasks[0]["upstream_outputs"] == ["Prepare quote: agent quote draft ready"]


def test_confirmed_task_default_human_assignee_is_used_for_later_human_subtasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    reviewer = client.post(
        "/api/v1/users",
        json={"name": "李晨", "role": "user", "department": "研发部", "position": "研发经理"},
    ).json()

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need human approval",
                subtasks=[
                    SubTask(
                        id="subtask_default_assignee",
                        title="确认研发方案",
                        description="需要人工确认研发方案是否通过",
                        assignee_type="human",
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "确认研发方案"},
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "确认研发方案",
            "description": "需要人工确认研发方案是否通过",
            "default_assignee_user_id": reviewer["id"],
            "default_assignee_user_name": reviewer["name"],
            "default_assignee_role": reviewer["role"],
        },
    ).json()

    assert paused["request_metadata"]["default_human_assignee"] == {
        "assignee_user_id": reviewer["id"],
        "assignee_user_name": "李晨",
        "assignee_role": "user",
    }
    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_user_id"] == reviewer["id"]
    assert human_subtask["assignee_user_name"] == "李晨"
    assert human_subtask["assignee_role"] == "user"
    queued = client.get(
        f"/api/v1/subtasks/human?assignee_user_id={reviewer['id']}"
    ).json()[0]
    assert queued["id"] == human_subtask["id"]
    assert queued["logical_key"] == "subtask_default_assignee"


def test_human_intent_pauses_for_default_assignee_when_planner_returns_no_subtasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.models import RoundPlan

    monkeypatch.setattr(
        "app.services.task_service.recognize_tasks_with_model",
        lambda _content, _agents: [
            {
                "title": "审核热点信息内容",
                "description": "对热点信息统计结果进行人工审核。",
                "confidence": 0.94,
                "suggested_assignee_type": "human",
                "suggested_agent_id": None,
            }
        ],
    )
    monkeypatch.setattr(
        "app.workflows.task_graph.plan_next_round_with_model",
        lambda _task, _agents: RoundPlan(
            should_continue=False,
            reason="没有可用 Agent",
            final_output="缺少热点数据抓取能力",
        ),
    )
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    reviewer = client.post(
        "/api/v1/users",
        json={"name": "李晨", "role": "user", "department": "研发部", "position": "研发经理"},
    ).json()
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "热点信息统计",
            "content": "统计今年后半年的热点信息，我要审核下内容后，再发给李晨",
        },
    ).json()["tasks"][0]

    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "热点信息统计",
            "description": "审核热点信息内容：对热点信息统计结果进行人工审核。",
            "default_assignee_user_id": reviewer["id"],
            "default_assignee_user_name": reviewer["name"],
            "default_assignee_role": reviewer["role"],
        },
    ).json()

    assert paused["task_status"] == "running"
    assert paused["current_node"] == "human_execution"
    human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["assignee_type"] == "human"
    assert human_subtask["title"] == "审核热点信息内容"
    assert human_subtask["assignee_user_id"] == reviewer["id"]
    assert human_subtask["assignee_user_name"] == "李晨"
    assert client.get(f"/api/v1/subtasks/human?assignee_user_id={reviewer['id']}").json()[0]["id"] == human_subtask["id"]


def test_human_subtask_result_resumes_task_flow(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need human approval",
                subtasks=[
                    SubTask(
                        id="subtask_human_resume",
                        title="Approve discount",
                        description="Human must approve the discount",
                        assignee_type="human",
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Approve discount"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Approve discount", "description": "Human must approve the discount"},
    ).json()
    assert paused["current_node"] == "human_execution"
    execution_id = paused["active_execution_id"]
    assert len(paused["executions"]) == 1
    stored_human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert stored_human_subtask["id"] != "subtask_human_resume"
    assert stored_human_subtask["logical_key"] == "subtask_human_resume"

    resumed = client.post(
        f"/api/v1/subtasks/{stored_human_subtask['id']}/result",
        json={"result_status": "succeeded", "output": "discount approved", "should_complete": True},
    ).json()

    assert resumed["task_status"] == "succeeded"
    assert resumed["current_node"] == "completion_judge"
    assert "discount approved" in resumed["context"]["summary"]
    human_subtask = resumed["context"]["rounds"][0]["subtasks"][0]
    assert human_subtask["status"] == "succeeded"
    assert human_subtask["output"] == "discount approved"
    assert resumed["active_execution_id"] == execution_id
    assert len(resumed["executions"]) == 1
    assert resumed["executions"][0]["id"] == execution_id
    human_artifact = next(
        artifact
        for artifact in resumed["artifacts"]
        if artifact["source_type"] == "subtask_output"
    )
    assert human_artifact["source_id"] == stored_human_subtask["id"]
    assert human_artifact["content"] == "discount approved"
    assert resumed["executions"][0]["artifacts"] == resumed["artifacts"]

    repeated = client.post(
        f"/api/v1/subtasks/{stored_human_subtask['id']}/result",
        json={"result_status": "failed", "output": "must not overwrite", "should_complete": True},
    )
    assert repeated.status_code == 409
    reloaded = client.get(f"/api/v1/tasks/{created['id']}").json()
    assert reloaded["final_output"] == resumed["final_output"]
    assert reloaded["executions"] == resumed["executions"]


def test_human_subtask_result_can_resume_task_flow_async(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    started: list[str] = []

    def _plan(task, agents):
        from app.core.models import RoundPlan, SubTask

        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                reason="Need human approval",
                subtasks=[
                    SubTask(
                        id="subtask_human_async_resume",
                        title="Approve risk",
                        description="Human must approve the risk summary",
                        assignee_type="human",
                    )
                ],
            )
        return RoundPlan(should_continue=False, reason="No remaining subtasks", final_output=task.context.summary)

    def _capture_background_start(self, task_id, expected_execution_id=None):
        started.append(task_id)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr("app.services.task_service.TaskService.start_background_task", _capture_background_start)

    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Approve risk"},
    ).json()["tasks"][0]
    paused = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={"title": "Approve risk", "description": "Human must approve the risk summary"},
    ).json()
    assert paused["current_node"] == "human_execution"
    stored_human_subtask = paused["context"]["rounds"][0]["subtasks"][0]
    assert stored_human_subtask["logical_key"] == "subtask_human_async_resume"

    response = client.post(
        f"/api/v1/subtasks/{stored_human_subtask['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "risk approved",
            "should_complete": True,
            "execution_mode": "async",
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["task_status"] == "running"
    assert submitted["current_node"] == "context_update"
    assert "risk approved" in submitted["context"]["summary"]
    assert started == [created["id"]]
    assert client.get("/api/v1/subtasks/human").json() == []


def test_task_level_result_completion_updates_final_output(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "人工介入闭环任务",
            "content": "需要人工介入后给出最终结论",
        },
    ).json()["tasks"][0]

    before = client.get(f"/api/v1/tasks/{created['id']}").json()
    response = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "人工介入后确认任务完成",
            "should_complete": True,
        },
    )

    assert response.status_code == 409
    assert client.get(f"/api/v1/tasks/{created['id']}").json() == before


def test_task_result_only_accepts_human_intervention_state(tmp_path: Path) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    service.store.save(task)
    before = task.model_dump(mode="json")

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "Forged delivery",
            "criterion_results": [
                {
                    "criterion_id": "criterion_reviewable",
                    "status": "passed",
                    "evidence_text": "Self asserted",
                }
            ],
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
    assert service.get_task(task.id).model_dump(mode="json") == before


def test_human_acceptance_requires_pending_report_for_active_execution(
    tmp_path: Path,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.HUMAN_INTERVENTION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    service.store.save(task)
    before = task.model_dump(mode="json")

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "Forged acceptance",
            "criterion_results": [
                {
                    "criterion_id": "criterion_reviewable",
                    "status": "passed",
                    "evidence_text": "Self asserted",
                }
            ],
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
    assert service.get_task(task.id).model_dump(mode="json") == before


def test_human_acceptance_rejects_pending_report_from_other_execution(
    tmp_path: Path,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    service.completion_service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Original reviewable delivery",
        reason="All automated checks passed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_text="Original reviewable delivery",
            )
        ],
    )
    assert task.completion_report is not None
    task.completion_report.execution_id = "execution_stale"
    execution.completion_report = task.completion_report.model_copy(deep=True)
    service.store.save(task)
    before = task.model_dump(mode="json")

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "Forged acceptance",
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
    assert service.get_task(task.id).model_dump(mode="json") == before


@pytest.mark.parametrize("result_status", ["partial"])
def test_task_level_result_preserves_non_success_terminal_status(tmp_path: Path, result_status: str) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Task requiring external completion"},
    ).json()["tasks"][0]
    task = app.state.task_service.get_task(created["id"])
    task.current_node = CurrentNode.HUMAN_INTERVENTION
    task.contract = TaskContract(
        goal="Complete external task",
        deliverable_goal="External result",
        success_criteria=[TaskContractItem(id="criterion_external", description="External result is available")],
        confirmed_at=utc_now(),
        legacy_inferred=True,
    )
    app.state.task_service.store.save(task)

    result = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": result_status,
            "output": "Only partial evidence is available",
            "completion_reason": f"External executor reported {result_status}",
        },
    )

    assert result.status_code == 200
    task = result.json()
    assert task["task_status"] == result_status
    assert task["completion_report"]["terminal_status"] == result_status
    assert task["completion_report"]["completion_reason"] == f"External executor reported {result_status}"


def test_task_level_blocked_result_remains_blocked(tmp_path: Path) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Task requiring external completion"},
    ).json()["tasks"][0]
    task = app.state.task_service.get_task(created["id"])
    task.current_node = CurrentNode.HUMAN_INTERVENTION
    task.contract = TaskContract(
        goal="Complete external task",
        deliverable_goal="External result",
        success_criteria=[TaskContractItem(id="criterion_external", description="External result is available")],
        confirmed_at=utc_now(),
        legacy_inferred=True,
    )
    app.state.task_service.store.save(task)

    response = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "blocked",
            "output": "Only partial evidence is available",
            "completion_reason": "External executor could not decide",
        },
    )

    assert response.status_code == 200
    blocked = response.json()
    assert blocked["task_status"] == "blocked"
    assert blocked["current_node"] == "completion_judge"
    assert blocked["completion_report"]["terminal_status"] == "blocked"
    assert blocked["completion_report"]["awaiting_human_decision"] is False


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("succeeded", "succeeded"), ("failed", "failed")],
)
def test_human_adjudication_finalizes_automatic_completion_gap(
    tmp_path: Path,
    decision: str,
    expected_status: str,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery for adjudication"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[TaskContractItem(id="criterion_reviewable", description="Delivery is reviewable")],
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(task, actor, CurrentNode.DISPATCH_DECISION)
    execution.started_at = utc_now()
    pending_report = service.completion_service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery output requiring review",
        reason="Automatic checks completed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.FAILED,
                reason="Evidence is inconclusive",
            )
        ],
    )
    service.store.save(task)

    assert pending_report.awaiting_human_decision is True
    assert execution.finished_at is None

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": decision,
            "output": "管理员确认最终结论",
            "should_complete": True,
            "metadata": {"human_adjudicated": True},
        },
    )

    assert response.status_code == 200
    adjudicated = response.json()
    assert adjudicated["task_status"] == expected_status
    assert adjudicated["current_node"] == "completion_judge"
    assert adjudicated["completion_report"]["terminal_status"] == expected_status
    assert adjudicated["completion_report"]["decided_by_type"] == "human"
    assert adjudicated["executions"][0]["finished_at"] is not None
    if decision == "succeeded":
        criterion_result = adjudicated["completion_report"]["criterion_results"][0]
        assert criterion_result["status"] == "passed"
        assert criterion_result["evidence_text"] == "管理员确认最终结论"
        assert "Human adjudication" in criterion_result["reason"]


def test_human_adjudication_can_supply_missing_output_on_second_submission(
    tmp_path: Path,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delayed output"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delayed output",
        deliverable_goal="Reviewable output",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="The output is reviewable",
            )
        ],
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    pending_report = service.completion_service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="",
        reason="Output is not available yet",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_text="Criterion is ready but output is missing",
            )
        ],
    )
    service.store.save(task)

    assert pending_report.awaiting_human_decision is True
    first = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "",
            "metadata": {"human_accepted": True},
        },
    )

    assert first.status_code == 200
    still_pending = first.json()
    assert still_pending["task_status"] == "running"
    assert still_pending["completion_report"]["awaiting_human_decision"] is True
    assert still_pending["completion_report"]["decided_by_type"] == "human"
    assert still_pending["completion_report"]["human_accepted"] is True

    second = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "管理员补齐最终输出",
            "metadata": {"human_accepted": True},
        },
    )

    assert second.status_code == 200
    completed = second.json()
    assert completed["task_status"] == "succeeded"
    assert completed["final_output"] == "管理员补齐最终输出"
    criterion_result = completed["completion_report"]["criterion_results"][0]
    assert criterion_result["status"] == "passed"
    assert criterion_result["evidence_text"] == "管理员补齐最终输出"


def test_task_result_rejects_duplicate_criterion_ids(tmp_path: Path) -> None:
    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery"},
    ).json()["tasks"][0]

    response = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "Delivery",
            "criterion_results": [
                {"criterion_id": "criterion_same", "status": "passed"},
                {"criterion_id": "  criterion_same  ", "status": "passed"},
            ],
        },
    )

    assert response.status_code == 422


def test_completed_task_does_not_wait_for_human_acceptance(
    tmp_path: Path,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare accepted delivery"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    report = service.completion_service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Original reviewable delivery",
        reason="All automated checks passed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_text="Original reviewable delivery",
            )
        ],
    )
    service.store.save(task)
    artifact_ids = [artifact.id for artifact in task.artifacts]
    before = task.model_dump(mode="json")

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert task.current_node == CurrentNode.COMPLETION_JUDGE
    assert execution.finished_at is not None

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "人工验收通过",
            "criterion_results": [
                {
                    "criterion_id": "criterion_reviewable",
                    "status": "failed",
                    "evidence_text": "Forged client evidence",
                }
            ],
            "artifact_ids": ["artifact_forged"],
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
    assert service.get_task(task.id).model_dump(mode="json") == before
    assert report.artifact_ids == artifact_ids


def test_human_acceptance_rejects_non_completing_request(tmp_path: Path) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare accepted delivery"},
    ).json()["tasks"][0]
    service = app.state.task_service
    task = service.get_task(created["id"])
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    execution.started_at = utc_now()
    service.completion_service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Original reviewable delivery",
        reason="All automated checks passed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_text="Original reviewable delivery",
            )
        ],
    )
    service.store.save(task)
    before = task.model_dump(mode="json")

    response = client.post(
        f"/api/v1/tasks/{task.id}/result",
        json={
            "result_status": "succeeded",
            "output": "人工验收通过",
            "should_complete": False,
            "metadata": {"human_accepted": True},
        },
    )

    assert response.status_code == 409
    assert service.get_task(task.id).model_dump(mode="json") == before


def test_background_completion_is_serialized_against_late_result_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    service = app.state.task_service
    created = service.create_request(
        TaskRequestCreate(
            source_type="business_system",
            content="Prepare accepted delivery",
        ),
        app.state.user_registry.get_user("root"),
    ).tasks[0]
    task = service.get_task(created.id)
    task.contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        success_criteria=[
            TaskContractItem(
                id="criterion_reviewable",
                description="Delivery is reviewable",
            )
        ],
        requires_human_acceptance=True,
        confirmed_at=utc_now(),
    )
    task.current_node = CurrentNode.DISPATCH_DECISION
    actor = app.state.user_registry.get_user("root")
    execution = service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.DISPATCH_DECISION,
    )
    service.store.save(task)
    claimed = service._claim_execution(task.id, execution.id)
    assert claimed is not None

    background_entered = Event()
    release_background = Event()
    acceptance_started = Event()
    acceptance_finished = Event()
    background_errors: list[Exception] = []
    acceptance_results: list[Task] = []
    acceptance_errors: list[Exception] = []

    def delayed_flow(active_task: Task) -> Task:
        active_task.context.summary = "Serialized background update"
        background_entered.set()
        assert release_background.wait(2)
        service.completion_service.finalize(
            active_task,
            candidate_status=TaskStatus.SUCCEEDED,
            output="Original reviewable delivery",
            reason="All automated checks passed",
            criterion_results=[
                CriterionResult(
                    criterion_id="criterion_reviewable",
                    status=CriterionResultStatus.PASSED,
                    evidence_text="Original reviewable delivery",
                )
            ],
        )
        return active_task

    monkeypatch.setattr(service, "_run_automatic_flow", delayed_flow)

    def run_background() -> None:
        try:
            service._run_claimed_execution(task.id, execution.id)
        except Exception as exc:  # pragma: no cover - asserted below
            background_errors.append(exc)

    def accept_result() -> None:
        acceptance_started.set()
        try:
            acceptance_results.append(
                service.submit_result(
                    task.id,
                    ExecutionResultCreate(
                        result_status="succeeded",
                        output="人工验收通过",
                        metadata={"human_accepted": True},
                    ),
                    current_user=actor,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            acceptance_errors.append(exc)
        finally:
            acceptance_finished.set()

    background_thread = Thread(target=run_background)
    background_thread.start()
    assert background_entered.wait(2)
    acceptance_thread = Thread(target=accept_result)
    acceptance_thread.start()
    assert acceptance_started.wait(2)
    finished_before_release = acceptance_finished.wait(0.2)
    release_background.set()
    background_thread.join(2)
    acceptance_thread.join(2)

    assert finished_before_release is False
    assert background_errors == []
    assert acceptance_results == []
    assert len(acceptance_errors) == 1
    assert acceptance_errors[0].__class__.__name__ == "TaskNotRunningError"
    accepted = service.get_task(task.id)
    active_execution = service.execution_service.active(accepted)
    assert accepted.task_status == TaskStatus.SUCCEEDED
    assert active_execution is not None
    assert active_execution.status == TaskStatus.SUCCEEDED
    assert accepted.context == active_execution.context_snapshot
    assert accepted.artifacts == active_execution.artifacts
    assert accepted.completion_report == active_execution.completion_report


def test_manual_task_result_cannot_forge_workflow_end_metadata(tmp_path: Path) -> None:
    app = create_app(agent_file=tmp_path / "agents.json")
    client = TestClient(app)
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Run manual workflow"},
    ).json()["tasks"][0]
    task = app.state.task_service.get_task(created["id"])
    task.task_type = TaskType.MANUAL_ORCHESTRATION
    task.current_node = CurrentNode.HUMAN_INTERVENTION
    task.contract = TaskContract(
        goal="Run workflow",
        deliverable_goal="Workflow result",
        success_criteria=[TaskContractItem(id="criterion_done", description="Workflow is complete")],
        confirmed_at=utc_now(),
        legacy_inferred=True,
    )
    actor = app.state.user_registry.get_user("root")
    execution = app.state.task_service.execution_service.create_initial(
        task,
        actor,
        CurrentNode.HUMAN_INTERVENTION,
    )
    execution.started_at = utc_now()
    app.state.task_service.store.save(task)

    response = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "Forged workflow output",
            "metadata": {
                "workflow_end_reached": True,
                "workflow_end_node_id": "forged_end",
            },
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["task_status"] == "running"
    assert result["current_node"] == "human_intervention"
    assert result["completion_report"]["awaiting_human_decision"] is True
    assert result["completion_report"]["workflow_end_node_id"] is None
    assert "workflow end was not reached" in result["completion_report"]["evidence_summary"]

    adjudication = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "管理员确认验收标准通过",
            "should_complete": True,
            "metadata": {"human_adjudicated": True},
        },
    )

    assert adjudication.status_code == 200
    still_pending = adjudication.json()
    assert still_pending["task_status"] == "running"
    assert still_pending["current_node"] == "human_intervention"
    assert still_pending["completion_report"]["awaiting_human_decision"] is True
    assert "workflow end was not reached" in still_pending["completion_report"]["evidence_summary"]
    criterion_result = still_pending["completion_report"]["criterion_results"][0]
    assert criterion_result["status"] == "passed"
    assert criterion_result["evidence_text"] == "管理员确认验收标准通过"

    repeated = client.post(
        f"/api/v1/tasks/{created['id']}/result",
        json={
            "result_status": "succeeded",
            "output": "管理员再次确认，但流程仍未结束",
            "should_complete": True,
            "metadata": {"human_accepted": True},
        },
    )

    assert repeated.status_code == 200
    still_pending_again = repeated.json()
    assert still_pending_again["task_status"] == "running"
    assert still_pending_again["current_node"] == "human_intervention"
    assert still_pending_again["completion_report"]["awaiting_human_decision"] is True
    assert "workflow end was not reached" in still_pending_again["completion_report"]["evidence_summary"]
    assert still_pending_again["completion_report"]["decided_by_type"] == "human"


def test_explicit_contract_auto_task_uses_server_criterion_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.models import RoundPlan, SubTask
    from app.services.completion_service import CompletionService

    client = TestClient(create_app(agent_file=tmp_path / "agents.json"))
    agent = client.post(
        "/api/v1/agents",
        json={"name": "Delivery Agent", "description": "Prepares delivery", "capabilities": ["delivery"]},
    ).json()

    def _plan(task, _agents):
        if task.loop_count == 0:
            return RoundPlan(
                should_continue=True,
                subtasks=[
                    SubTask(
                        id="subtask_delivery",
                        title="Prepare delivery",
                        description="Prepare reviewable delivery",
                        assigned_agent_id=agent["id"],
                    )
                ],
            )
        return RoundPlan(should_continue=False, final_output=task.context.summary)

    monkeypatch.setattr("app.workflows.task_graph.plan_next_round_with_model", _plan)
    monkeypatch.setattr(
        "app.workflows.task_graph.execute_subtask_with_tools_model",
        lambda *_args: ([], "Reviewable delivery"),
    )
    monkeypatch.setattr(
        CompletionService,
        "evaluate_criteria",
        lambda _self, _task, output: [
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_text=output,
                reason="Server evaluator passed the criterion",
            )
        ],
        raising=False,
    )
    created = client.post(
        "/api/v1/tasks/requests",
        json={"source_type": "business_system", "content": "Prepare delivery"},
    ).json()["tasks"][0]

    confirmed = client.post(
        f"/api/v1/tasks/{created['id']}/confirm",
        json={
            "title": "Prepare delivery",
            "description": "Prepare reviewable delivery",
            "contract": {
                "goal": "Prepare delivery",
                "deliverable_goal": "Reviewable delivery",
                "success_criteria": [
                    {"id": "criterion_reviewable", "description": "Delivery is reviewable"}
                ],
            },
        },
    ).json()

    assert confirmed["task_status"] == "succeeded", confirmed["completion_report"]
    assert confirmed["completion_report"]["criterion_results"][0]["evidence_text"] == "Reviewable delivery"
