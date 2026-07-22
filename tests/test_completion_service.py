from pathlib import Path

import pytest
from pydantic import ValidationError
import app.core.models as core_models
from app.services import deliverable_materializer as materializer_module

from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CriterionResultStatus,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
    TaskType,
)
from app.core.models import (
    Artifact,
    CriterionResult,
    DeliverableResult,
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    TaskRound,
    ToolExecutionResult,
    utc_now,
)
from app.services.completion_service import CompletionService
from app.services.artifact_service import ArtifactService
from app.services.deliverable_materializer import (
    DeliverableMaterializer,
    MaterializedDeliverable,
)


def test_completion_report_supports_structured_deliverable_results() -> None:
    assert hasattr(core_models, "DeliverableResult")
    assert "deliverable_results" in core_models.CompletionReport.model_fields


def _contract(
    *,
    legacy: bool = False,
    requires_human_acceptance: bool = False,
    with_deliverable_requirements: bool = False,
    deliverable_kind: str = "text",
    deliverable_format: str | None = None,
    deliverable_filename: str = "",
) -> TaskContract:
    return TaskContract(
        goal="Prepare a delivery plan",
        deliverable_goal="A reviewable plan",
        deliverable_kind=deliverable_kind,
        deliverable_format=deliverable_format,
        deliverable_filename=deliverable_filename,
        deliverable_requirements=(
            [
                TaskContractItem(id="requirement_summary", description="Contains summary"),
                TaskContractItem(id="requirement_risks", description="Contains risks"),
            ]
            if with_deliverable_requirements
            else []
        ),
        success_criteria=[TaskContractItem(id="criterion_reviewable", description="The plan is reviewable")],
        requires_human_acceptance=requires_human_acceptance,
        confirmed_at=utc_now(),
        legacy_inferred=legacy,
    )


def _task(
    *,
    contract: TaskContract | None = None,
    task_type: TaskType = TaskType.AUTO_PLANNING,
    subtasks: list[SubTask] | None = None,
) -> Task:
    now = utc_now()
    execution = TaskExecution(
        id="execution_1",
        task_id="task_1",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
        started_at=now,
    )
    rounds = []
    if subtasks is not None:
        rounds = [TaskRound(round_index=1, subtasks=subtasks)]
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare a delivery plan",
        task_type=task_type,
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=contract,
        executions=[execution],
        active_execution_id=execution.id,
        context={"rounds": rounds},
        created_at=now,
        updated_at=now,
    )


def _passed_criterion() -> CriterionResult:
    return CriterionResult(
        criterion_id="criterion_reviewable",
        status=CriterionResultStatus.PASSED,
        evidence_text="Reviewed output",
    )


def _file_contract(
    *,
    requires_human_acceptance: bool = False,
    with_deliverable_requirements: bool = False,
) -> TaskContract:
    return _contract(
        requires_human_acceptance=requires_human_acceptance,
        with_deliverable_requirements=with_deliverable_requirements,
        deliverable_kind="file",
        deliverable_format="markdown",
        deliverable_filename="delivery.md",
    )


def _register_managed_file(
    task: Task,
    path: Path,
    *,
    uri: str | None = None,
) -> tuple[ArtifactService, Artifact]:
    content = "Managed delivery"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    artifact_service = ArtifactService()
    artifact = artifact_service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=path,
            content=content,
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )
    if uri is not None:
        artifact = artifact_service.replace_current(
            task,
            artifact.model_copy(update={"uri": uri}),
        )
    return artifact_service, artifact


@pytest.mark.parametrize(
    "candidate_status",
    [TaskStatus.FAILED, TaskStatus.PARTIAL, TaskStatus.CANCELLED],
)
def test_non_success_terminal_status_is_preserved_and_execution_report_is_sealed(
    candidate_status: TaskStatus,
) -> None:
    task = _task(contract=_contract())

    report = CompletionService().finalize(
        task,
        candidate_status=candidate_status,
        output="Available output",
        reason=f"Candidate ended as {candidate_status.value}",
        decided_by_type="human",
        decided_by_id="user_1",
    )

    assert task.task_status == candidate_status
    assert report.terminal_status == candidate_status
    assert report.completion_reason == f"Candidate ended as {candidate_status.value}"
    assert task.completion_report == report
    assert task.executions[0].completion_report == report
    assert task.executions[0].status == candidate_status
    assert task.executions[0].finished_at is not None


def test_blocked_candidate_waits_for_human_adjudication() -> None:
    task = _task(contract=_contract())

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.BLOCKED,
        output="Available output",
        reason="Automatic completion is inconclusive",
    )

    assert task.task_status == TaskStatus.RUNNING
    assert task.current_node == CurrentNode.HUMAN_INTERVENTION
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.automatic_gaps == ["Automatic completion is inconclusive"]
    assert task.executions[0].finished_at is None


def test_succeeded_candidate_with_empty_output_waits_for_human_adjudication() -> None:
    task = _task(contract=_contract(legacy=True))

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="   ",
        reason="Work completed",
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert "output" in report.evidence_summary.lower()


def test_explicit_contract_requires_passed_evidence_for_every_criterion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_contract())
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_success_criteria_with_model",
        lambda *_args, **_kwargs: [],
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Work completed",
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.criterion_results == [
        CriterionResult(
            criterion_id="criterion_reviewable",
            status=CriterionResultStatus.PENDING,
            reason="Missing passed criterion evidence",
        )
    ]


def test_explicit_contract_succeeds_with_passed_criterion_evidence() -> None:
    task = _task(contract=_contract())

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
    )

    assert task.task_status == TaskStatus.SUCCEEDED
    assert report.criterion_results == [_passed_criterion()]


def test_legacy_contract_infers_passed_evidence_from_non_empty_output() -> None:
    task = _task(contract=_contract(legacy=True))

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Legacy task completed",
    )

    assert task.task_status == TaskStatus.SUCCEEDED
    assert report.criterion_results[0].status == CriterionResultStatus.PASSED
    assert report.criterion_results[0].evidence_text == "Delivery plan"


@pytest.mark.parametrize("status", [CriterionResultStatus.FAILED, CriterionResultStatus.PENDING])
def test_legacy_contract_preserves_explicit_non_passed_evidence(status: CriterionResultStatus) -> None:
    task = _task(contract=_contract(legacy=True))

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Legacy task completed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=status,
                reason="Explicit evaluator result",
            )
        ],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.criterion_results[0].status == status
    assert "criterion_reviewable" in report.evidence_summary


def test_criterion_result_strips_non_empty_id() -> None:
    result = CriterionResult(criterion_id="  criterion_reviewable  ", status=CriterionResultStatus.PASSED)

    assert result.criterion_id == "criterion_reviewable"


def test_criterion_result_rejects_empty_id() -> None:
    with pytest.raises(ValidationError, match="criterion_id"):
        CriterionResult(criterion_id="   ", status=CriterionResultStatus.PASSED)


def test_criterion_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CriterionResult.model_validate(
            {
                "criterion_id": "criterion_reviewable",
                "status": "passed",
                "unexpected": True,
            }
        )


def test_human_acceptance_requirement_waits_without_sealing_execution() -> None:
    task = _task(contract=_contract(requires_human_acceptance=True))

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Work completed",
        criterion_results=[_passed_criterion()],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert task.current_node == CurrentNode.HUMAN_INTERVENTION
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.human_accepted is False
    assert "human acceptance" in report.evidence_summary.lower()
    assert report.criterion_results == [_passed_criterion()]
    assert len(report.artifact_ids) == 1
    assert task.executions[0].status == TaskStatus.RUNNING
    assert task.executions[0].current_node == CurrentNode.HUMAN_INTERVENTION
    assert task.executions[0].finished_at is None
    assert task.executions[0].completion_report == report


def test_approved_human_subtask_does_not_replace_explicit_final_acceptance() -> None:
    approved = SubTask(
        id="human_approval",
        title="Approve",
        description="Approve delivery",
        assignee_type="human",
        status=TaskStatus.SUCCEEDED,
        result_metadata={"decision": "approved"},
    )
    task = _task(contract=_contract(requires_human_acceptance=True), subtasks=[approved])

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Work completed",
        criterion_results=[_passed_criterion()],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert task.current_node == CurrentNode.HUMAN_INTERVENTION
    assert report.terminal_status == TaskStatus.RUNNING
    assert report.human_accepted is False


def test_manual_workflow_must_reach_end_node() -> None:
    task = _task(contract=_contract(legacy=True), task_type=TaskType.MANUAL_ORCHESTRATION)

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="No runnable nodes",
        workflow_end_reached=False,
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.workflow_end_node_id is None
    assert "workflow end" in report.evidence_summary.lower()


@pytest.mark.parametrize(
    "subtask_status",
    [TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.PARTIAL],
)
def test_succeeded_candidate_requires_human_adjudication_for_incomplete_subtasks(subtask_status: TaskStatus) -> None:
    subtask = SubTask(
        id="subtask_1",
        title="Step",
        description="Required step",
        status=subtask_status,
    )
    task = _task(contract=_contract(legacy=True), subtasks=[subtask])

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="Work completed",
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert subtask_status.value in report.evidence_summary


def test_completion_registers_final_output_and_selects_all_current_artifacts_by_default() -> None:
    task = _task(contract=_contract())

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
        artifact_ids=None,
    )

    assert task.task_status == TaskStatus.SUCCEEDED
    assert len(task.artifacts) == 1
    assert report.artifact_ids == [task.artifacts[0].id]
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.VALID
    assert task.executions[0].artifacts == task.artifacts


def test_completion_explicit_empty_artifact_selection_blocks_success() -> None:
    task = _task(contract=_contract())

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
        artifact_ids=[],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert len(task.artifacts) == 1
    assert report.artifact_ids == []
    assert "artifact" in report.evidence_summary.lower()


def test_completion_requires_selected_artifacts_to_cover_every_deliverable_requirement() -> None:
    task = _task(contract=_contract(with_deliverable_requirements=True))
    subtask_artifact = ArtifactService().register_subtask_output(
        task,
        SubTask(id="subtask_1", title="Draft", description="Draft output"),
        "Draft delivery",
    )
    assert subtask_artifact is not None

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Final delivery",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
        artifact_ids=[subtask_artifact.id],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.artifact_ids == [subtask_artifact.id]
    assert "requirement_summary" in report.evidence_summary
    assert "requirement_risks" in report.evidence_summary


@pytest.mark.parametrize("artifact_id", ["artifact_unknown", "input_attachment_1", "artifact_old"])
def test_completion_rejects_unknown_input_or_old_execution_artifact_ids(artifact_id: str) -> None:
    task = _task(contract=_contract())
    task.context.artifacts.append("input_attachment_1")
    task.artifacts.append(
        Artifact(
            id="artifact_old",
            task_id=task.id,
            execution_id="execution_old",
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.TASK_RESULT,
            source_id="old_task_result",
            name="Old output",
            content="Old delivery",
            checksum="sha256:old",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=utc_now(),
        )
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Current delivery",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact_id],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.artifact_ids == []
    assert artifact_id in report.evidence_summary


@pytest.mark.parametrize(
    "validation_status",
    [ArtifactValidationStatus.PENDING, ArtifactValidationStatus.INVALID],
)
def test_completion_rejects_non_valid_selected_artifact(
    validation_status: ArtifactValidationStatus,
) -> None:
    task = _task(contract=_contract())
    artifact = ArtifactService().register_task_output(task, "Delivery plan")
    assert artifact is not None
    artifact.validation_status = validation_status
    artifact.validation_reason = "Validation not complete"

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="All checks passed",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.artifact_ids == []
    assert validation_status.value in report.evidence_summary


def test_completion_rejects_criterion_evidence_outside_selected_artifacts() -> None:
    task = _task(contract=_contract())
    artifact_service = ArtifactService()
    selected = artifact_service.register_task_output(task, "Delivery plan")
    unselected = artifact_service.register_subtask_output(
        task,
        SubTask(id="subtask_1", title="Draft", description="Draft output"),
        "Draft delivery",
    )
    assert selected is not None and unselected is not None

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Delivery plan",
        reason="All checks passed",
        criterion_results=[
            CriterionResult(
                criterion_id="criterion_reviewable",
                status=CriterionResultStatus.PASSED,
                evidence_artifact_ids=[unselected.id],
                evidence_text="Draft evidence",
            )
        ],
        artifact_ids=[selected.id],
    )

    assert task.task_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.artifact_ids == [selected.id]
    assert unselected.id in report.evidence_summary


def test_cancelled_task_without_active_execution_does_not_create_artifact() -> None:
    now = utc_now()
    task = Task(
        id="task_cancelled",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Draft",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.HUMAN_CONFIRMATION,
        created_at=now,
        updated_at=now,
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.CANCELLED,
        output="Cancelled before confirmation",
        reason="Cancelled before confirmation",
    )

    assert report.terminal_status == TaskStatus.CANCELLED
    assert task.artifacts == []
    assert report.artifact_ids == []


def test_pdf_requirement_is_not_satisfied_by_generic_done_text(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    contract.deliverable_requirements = [
        TaskContractItem(id="requirement_pdf", description="Provide a PDF file")
    ]
    task = _task(contract=contract)
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        lambda _task, _artifacts: None,
        raising=False,
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="done",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.deliverable_results[0].requirement_id == "requirement_pdf"
    assert report.deliverable_results[0].status == CriterionResultStatus.PENDING


def test_valid_pdf_with_explicit_requirement_mapping_can_succeed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    contract.deliverable_requirements = [
        TaskContractItem(id="requirement_pdf", description="Provide a PDF file")
    ]
    task = _task(contract=contract)
    pdf_path = tmp_path / "delivery.pdf"
    pdf_path.write_bytes(b"%PDF delivery")
    artifact = ArtifactService().register_tool_result(
        task,
        SubTask(id="subtask_pdf", title="Create PDF", description="Create PDF"),
        ToolExecutionResult(
            tool_execution_id="tool_pdf",
            tool_name="write_pdf",
            tool_type="file_write",
            success=True,
            result=str(pdf_path),
        ),
    )
    assert artifact is not None
    task.artifacts[0] = artifact.model_copy(
        update={"deliverable_requirement_ids": ["requirement_pdf"]}
    )
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        lambda *_args: (_ for _ in ()).throw(AssertionError("covered requirement must not call model")),
        raising=False,
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="done",
        reason="done",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert report.deliverable_results == [
        DeliverableResult(
            requirement_id="requirement_pdf",
            status=CriterionResultStatus.PASSED,
            artifact_ids=[artifact.id],
            reason="Covered by explicit artifact mapping",
        )
    ]


def test_valid_pdf_with_model_mapping_is_associated_and_can_succeed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    contract.deliverable_requirements = [
        TaskContractItem(id="requirement_pdf", description="Provide a PDF file")
    ]
    task = _task(contract=contract)
    pdf_path = tmp_path / "delivery.pdf"
    pdf_path.write_bytes(b"%PDF delivery")
    artifact = ArtifactService().register_tool_result(
        task,
        SubTask(id="subtask_pdf", title="Create PDF", description="Create PDF"),
        ToolExecutionResult(
            tool_execution_id="tool_pdf",
            tool_name="write_pdf",
            tool_type="file_write",
            success=True,
            result=str(pdf_path),
        ),
    )
    assert artifact is not None
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        lambda _task, _artifacts: [
            DeliverableResult(
                requirement_id="requirement_pdf",
                status=CriterionResultStatus.PASSED,
                artifact_ids=[artifact.id],
                reason="PDF artifact satisfies requirement",
            )
        ],
        raising=False,
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="done",
        reason="done",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert report.deliverable_results[0].artifact_ids == [artifact.id]
    assert task.artifacts[0].deliverable_requirement_ids == ["requirement_pdf"]


@pytest.mark.parametrize(
    "status",
    [CriterionResultStatus.FAILED, CriterionResultStatus.PENDING],
)
def test_completion_drops_unselected_artifact_ids_from_nonpassed_deliverable_results(
    monkeypatch: pytest.MonkeyPatch,
    status: CriterionResultStatus,
) -> None:
    contract = _contract()
    contract.deliverable_requirements = [
        TaskContractItem(id="requirement_summary", description="Provide a summary")
    ]
    task = _task(contract=contract)
    selected = ArtifactService().register_task_output(task, "Summary")
    assert selected is not None
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        lambda _task, _artifacts: [
            DeliverableResult(
                requirement_id="requirement_summary",
                status=status,
                artifact_ids=["artifact_not_selected"],
                reason="External artifact reference",
            )
        ],
    )

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Summary",
        reason="done",
        criterion_results=[_passed_criterion()],
        artifact_ids=[selected.id],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.deliverable_results[0].status == CriterionResultStatus.PENDING
    assert report.deliverable_results[0].artifact_ids == []
    assert "artifact_not_selected" not in report.model_dump_json()


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_completion_revalidates_selected_file_artifact_before_success(
    tmp_path,
    change: str,
) -> None:
    task = _task(contract=_contract())
    file_path = tmp_path / "delivery.pdf"
    file_path.write_bytes(b"original")
    artifact = ArtifactService().register_tool_result(
        task,
        SubTask(id="subtask_pdf", title="Create PDF", description="Create PDF"),
        ToolExecutionResult(
            tool_execution_id="tool_pdf",
            tool_name="write_pdf",
            tool_type="file_write",
            success=True,
            result=str(file_path),
        ),
    )
    assert artifact is not None
    if change == "modify":
        file_path.write_bytes(b"changed")
    else:
        file_path.unlink()

    report = CompletionService().finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="done",
        reason="done",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID


def test_file_delivery_content_uses_output_without_completed_round() -> None:
    task = _task(contract=_file_contract())
    task.context.summary = "Attachment and dependency context"

    content = CompletionService.delivery_content(task, "  # Final report  ")

    assert content == "# Final report"


@pytest.mark.parametrize(
    ("status", "subtask_output", "summary"),
    [
        (TaskStatus.RUNNING, "", "Attachment and dependency context"),
        (
            TaskStatus.FAILED,
            "Agent execution failed",
            "Attachment and dependency context\nFAILED: Agent execution failed",
        ),
    ],
)
def test_file_delivery_content_uses_output_for_pending_or_failed_only_round(
    status: TaskStatus,
    subtask_output: str,
    summary: str,
) -> None:
    task = _task(contract=_file_contract())
    task.context.rounds = [
        TaskRound(
            round_index=1,
            context_before="Attachment and dependency context",
            subtasks=[
                SubTask(
                    id="round_subtask",
                    title="Prepare report",
                    description="Prepare report",
                    assignee_type="human" if status == TaskStatus.RUNNING else "agent",
                    status=status,
                    output=subtask_output,
                )
            ],
            context_after=summary,
        )
    ]
    task.context.summary = summary

    content = CompletionService.delivery_content(task, "  # Final report  ")

    assert content == "# Final report"


def test_file_delivery_content_prefers_summary_from_completed_round() -> None:
    task = _task(contract=_file_contract())
    merged_summary = "Attachment context\nMerged round report"
    task.context.rounds = [
        TaskRound(
            round_index=1,
            context_before="Attachment context",
            subtasks=[
                SubTask(
                    id="completed_subtask",
                    title="Prepare report",
                    description="Prepare report",
                    status=TaskStatus.SUCCEEDED,
                    output="Round report body",
                )
            ],
            context_after=merged_summary,
        )
    ]
    task.context.summary = merged_summary

    content = CompletionService.delivery_content(task, "# Short conclusion")

    assert content == merged_summary


def test_file_delivery_content_uses_output_when_successful_round_output_is_empty() -> None:
    task = _task(contract=_file_contract())
    inherited_summary = "Attachment and dependency context"
    task.context.rounds = [
        TaskRound(
            round_index=1,
            context_before=inherited_summary,
            subtasks=[
                SubTask(
                    id="empty_success",
                    title="Prepare report",
                    description="Prepare report",
                    status=TaskStatus.SUCCEEDED,
                    output="",
                )
            ],
            context_after=inherited_summary,
        )
    ]
    task.context.summary = inherited_summary

    content = CompletionService.delivery_content(task, "  # Final report  ")

    assert content == "# Final report"


def test_file_delivery_content_uses_output_when_historical_success_has_pending_human() -> None:
    task = _task(contract=_file_contract())
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="completed_subtask",
                    title="Draft report",
                    description="Draft report",
                    status=TaskStatus.SUCCEEDED,
                    output="Historical report body",
                )
            ],
            context_after="Historical report body",
        ),
        TaskRound(
            round_index=2,
            context_before="Historical report body",
            subtasks=[
                SubTask(
                    id="pending_human",
                    title="Approve report",
                    description="Approve report",
                    assignee_type="human",
                    status=TaskStatus.RUNNING,
                )
            ],
            context_after="Historical report body",
        ),
    ]
    task.context.summary = "Historical report body"

    content = CompletionService.delivery_content(task, "  # Final report  ")

    assert content == "# Final report"


def test_file_delivery_materializes_completed_round_summary_to_managed_artifact(
    tmp_path: Path,
) -> None:
    task = _task(contract=_file_contract())
    task.context.summary = "  Canonical delivery summary  "
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="completed_subtask",
                    title="Prepare report",
                    description="Prepare report",
                    status=TaskStatus.SUCCEEDED,
                    output="Canonical delivery summary",
                )
            ],
            context_after="Canonical delivery summary",
        )
    ]
    service = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    )

    report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert len(report.artifact_ids) == 1
    artifact = task.artifacts[0]
    assert artifact.kind == ArtifactKind.FILE
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.metadata["managed_final_delivery"] is True
    assert artifact.content == "Canonical delivery summary"
    assert Path(artifact.uri.removeprefix("file://")).read_text(encoding="utf-8") == (
        "Canonical delivery summary"
    )


def test_finalize_uses_normalized_delivery_content_everywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract())
    merged_summary = "# Merged final report"
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="completed_subtask",
                    title="Prepare report",
                    description="Prepare report",
                    status=TaskStatus.SUCCEEDED,
                    output="Round report body",
                )
            ],
            context_after=merged_summary,
        )
    ]
    task.context.summary = merged_summary
    service = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    )
    evaluated_outputs: list[str] = []
    original_evaluate_success = service._evaluate_success

    def capture_evaluate_success(
        task_arg: Task,
        output: str,
        *args,
        **kwargs,
    ) -> tuple[list[CriterionResult], list[str]]:
        evaluated_outputs.append(output)
        return original_evaluate_success(task_arg, output, *args, **kwargs)

    monkeypatch.setattr(service, "_evaluate_success", capture_evaluate_success)

    report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="  Short completion conclusion  ",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert evaluated_outputs == [merged_summary]
    artifact = task.artifacts[0]
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.content == merged_summary
    assert Path(artifact.uri.removeprefix("file://")).read_text(encoding="utf-8") == (
        merged_summary
    )
    assert task.final_output == merged_summary
    assert task.executions[0].final_output == merged_summary


def test_file_delivery_materializes_output_when_summary_has_no_completed_round(
    tmp_path: Path,
) -> None:
    task = _task(contract=_file_contract())
    task.context.summary = "Attachment and dependency context"
    service = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    )

    report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="  # Final report  ",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    artifact = task.artifacts[0]
    assert artifact.content == "# Final report"
    assert Path(artifact.uri.removeprefix("file://")).read_text(encoding="utf-8") == (
        "# Final report"
    )


def test_file_delivery_materializes_output_when_context_summary_is_empty(
    tmp_path: Path,
) -> None:
    task = _task(contract=_file_contract())
    service = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    )

    report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="  Output fallback  ",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    artifact = task.artifacts[0]
    assert artifact.content == "Output fallback"
    assert Path(artifact.uri.removeprefix("file://")).read_text(encoding="utf-8") == (
        "Output fallback"
    )


def test_file_delivery_text_cannot_replace_explicitly_excluded_managed_file(
    tmp_path: Path,
) -> None:
    task = _task(contract=_file_contract())
    artifact_service = ArtifactService()
    text_artifact = artifact_service.register_subtask_output(
        task,
        SubTask(id="subtask_text", title="Text", description="Text output"),
        "Text-only output",
    )
    assert text_artifact is not None
    service = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    )

    report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
        artifact_ids=[text_artifact.id],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == [text_artifact.id]
    assert any(
        artifact.metadata.get("managed_final_delivery") is True
        for artifact in task.artifacts
    )
    assert "valid final delivery file" in report.evidence_summary.lower()


def test_file_delivery_materializer_oserror_becomes_sanitized_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract())
    materializer = DeliverableMaterializer(tmp_path / "outputs")
    sensitive_path = tmp_path / "private" / "customer-secret.md"

    def fail_materialization(_task: Task, _content: str) -> MaterializedDeliverable:
        raise OSError(f"could not write {sensitive_path}")

    monkeypatch.setattr(materializer, "materialize", fail_materialization)
    report = CompletionService(deliverable_materializer=materializer).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    serialized_report = report.model_dump_json()
    assert report.terminal_status == TaskStatus.RUNNING
    assert "could not be written" in report.evidence_summary.lower()
    assert str(sensitive_path) not in serialized_report
    assert task.artifacts == []


def test_file_delivery_blocks_when_secure_dir_fd_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    monkeypatch.setattr(
        materializer_module,
        "_SUPPORTS_SECURE_DIR_FD",
        False,
    )

    report = CompletionService(
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert "could not be written" in report.evidence_summary.lower()
    assert task.artifacts == []
    assert not output_root.exists()


def test_file_delivery_materializer_valueerror_becomes_sanitized_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract())
    materializer = DeliverableMaterializer(tmp_path / "outputs")
    sensitive_value = "customer-secret-filename.md"

    def reject_materialization(_task: Task, _content: str) -> MaterializedDeliverable:
        raise ValueError(f"unsafe deliverable {sensitive_value}")

    monkeypatch.setattr(materializer, "materialize", reject_materialization)
    report = CompletionService(deliverable_materializer=materializer).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    serialized_report = report.model_dump_json()
    assert report.terminal_status == TaskStatus.RUNNING
    assert "materialization was rejected" in report.evidence_summary.lower()
    assert sensitive_value not in serialized_report
    assert task.artifacts == []


@pytest.mark.parametrize(
    ("candidate_status", "expected_status"),
    [
        (TaskStatus.FAILED, TaskStatus.FAILED),
        (TaskStatus.BLOCKED, TaskStatus.RUNNING),
        (TaskStatus.PARTIAL, TaskStatus.PARTIAL),
        (TaskStatus.CANCELLED, TaskStatus.CANCELLED),
    ],
)
def test_non_success_file_delivery_preserves_text_without_materializing_file(
    tmp_path: Path,
    candidate_status: TaskStatus,
    expected_status: TaskStatus,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    task.context.rounds = [
        TaskRound(
            round_index=1,
            subtasks=[
                SubTask(
                    id="historical_success",
                    title="Draft report",
                    description="Draft report",
                    status=TaskStatus.SUCCEEDED,
                    output="Historical report body",
                )
            ],
            context_after="Historical report body",
        )
    ]
    task.context.summary = "Historical report body"

    report = CompletionService(
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=candidate_status,
        output="Partial workflow output",
        reason=candidate_status.value,
    )

    assert report.terminal_status == expected_status
    assert report.awaiting_human_decision is (candidate_status == TaskStatus.BLOCKED)
    assert len(task.artifacts) == 1
    artifact = task.artifacts[0]
    assert artifact.kind == ArtifactKind.TEXT
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.source_id == task.id
    assert artifact.content == "Partial workflow output"
    assert artifact.metadata.get("managed_final_delivery") is not True
    assert report.artifact_ids == [artifact.id]
    assert task.final_output == "Partial workflow output"
    assert task.executions[0].final_output == "Partial workflow output"
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("case", "expected_gap"),
    [
        ("outside_root", "location is invalid"),
        ("wrong_format", "format"),
        ("wrong_media_type", "media type"),
    ],
)
def test_file_delivery_gate_rejects_path_format_or_media_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_gap: str,
) -> None:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    materializer = DeliverableMaterializer(output_root)
    task = _task(contract=_file_contract())
    delivery_dir = output_root / task.id / task.active_execution_id
    delivery_dir.mkdir(parents=True)
    if case == "outside_root":
        path = tmp_path / "outside.md"
        media_type = "text/markdown"
        delivery_format = "markdown"
    elif case == "wrong_format":
        path = delivery_dir / "delivery.md"
        media_type = "text/markdown"
        delivery_format = "text"
    else:
        path = delivery_dir / "delivery.md"
        media_type = "text/plain"
        delivery_format = "markdown"
    path.write_text("Managed delivery", encoding="utf-8")

    monkeypatch.setattr(
        materializer,
        "materialize",
        lambda _task, _content: MaterializedDeliverable(
            path=path,
            content="Managed delivery",
            media_type=media_type,
            delivery_format=delivery_format,
        ),
    )

    report = CompletionService(deliverable_materializer=materializer).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert expected_gap in report.evidence_summary.lower()
    if case == "outside_root":
        assert report.artifact_ids == []
        assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID


def test_file_delivery_revalidation_invalidates_non_local_managed_uri(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    local_path = output_root / task.id / task.active_execution_id / "report.md"
    artifact_service, artifact = _register_managed_file(
        task,
        local_path,
        uri="https://example.invalid/report.md",
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_invalidates_managed_file_outside_output_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    artifact_service, artifact = _register_managed_file(
        task,
        tmp_path / "outside" / "report.md",
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_rejects_managed_file_from_sibling_task_directory(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    sibling_path = output_root / "other_task" / task.active_execution_id / "report.md"
    artifact_service, artifact = _register_managed_file(
        task,
        sibling_path,
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_rejects_managed_file_from_sibling_execution_directory(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    sibling_path = output_root / task.id / "other_execution" / "report.md"
    artifact_service, artifact = _register_managed_file(task, sibling_path)

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_rejects_filename_not_matching_contract(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    report_path = output_root / task.id / task.active_execution_id / "report.md"
    artifact_service, artifact = _register_managed_file(task, report_path)

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "filename is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_rejects_artifact_name_not_matching_uri(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_path = output_root / task.id / task.active_execution_id / "delivery.md"
    artifact_service, artifact = _register_managed_file(task, delivery_path)
    artifact = artifact_service.replace_current(
        task,
        artifact.model_copy(update={"name": "report.md"}),
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "filename is invalid" in report.evidence_summary.lower()


def test_file_delivery_accepts_default_task_id_filename(tmp_path: Path) -> None:
    task = _task(
        contract=_contract(
            deliverable_kind="file",
            deliverable_format="markdown",
            deliverable_filename="",
        )
    )

    report = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert task.artifacts[0].name == f"{task.id}.md"


def test_file_delivery_revalidation_rejects_dot_dot_managed_file_uri(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_dir = output_root / task.id / task.active_execution_id
    (delivery_dir / "nested").mkdir(parents=True)
    delivery_path = delivery_dir / "report.md"
    dot_dot_uri = (delivery_dir / "nested" / ".." / "report.md").as_uri()
    artifact_service, artifact = _register_managed_file(
        task,
        delivery_path,
        uri=dot_dot_uri,
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_rejects_managed_file_symlink(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_dir = output_root / task.id / task.active_execution_id
    target_path = delivery_dir / "actual.md"
    artifact_service, artifact = _register_managed_file(task, target_path)
    symlink_path = delivery_dir / "delivery.md"
    symlink_path.symlink_to(target_path.name)
    artifact = artifact_service.replace_current(
        task,
        artifact.model_copy(update={"uri": symlink_path.as_uri()}),
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


@pytest.mark.parametrize("case", ["outside_root", "file_symlink"])
def test_invalid_managed_location_is_rejected_before_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    if case == "outside_root":
        artifact_service, artifact = _register_managed_file(
            task,
            tmp_path / "outside" / "delivery.md",
        )
    else:
        delivery_dir = output_root / task.id / task.active_execution_id
        target_path = delivery_dir / "actual.md"
        artifact_service, artifact = _register_managed_file(task, target_path)
        symlink_path = delivery_dir / "delivery.md"
        symlink_path.symlink_to(target_path.name)
        artifact = artifact_service.replace_current(
            task,
            artifact.model_copy(update={"uri": symlink_path.as_uri()}),
        )

    def reject_read(_path: Path) -> bytes:
        raise AssertionError("invalid managed location must not be read")

    monkeypatch.setattr(Path, "read_bytes", reject_read)

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_managed_file_revalidation_does_not_use_path_based_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_path = output_root / task.id / task.active_execution_id / "delivery.md"
    artifact_service, artifact = _register_managed_file(task, delivery_path)

    def reject_path_read(_path: Path) -> bytes:
        raise AssertionError("managed final delivery must use dir_fd reads")

    monkeypatch.setattr(Path, "read_bytes", reject_path_read)

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert report.artifact_ids == [artifact.id]


def test_file_delivery_rejects_materialized_file_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_dir = output_root / task.id / task.active_execution_id
    delivery_dir.mkdir(parents=True)
    target_path = delivery_dir / "actual.md"
    target_path.write_text("Managed delivery", encoding="utf-8")
    symlink_path = delivery_dir / "delivery.md"
    symlink_path.symlink_to(target_path.name)
    materializer = DeliverableMaterializer(output_root)
    monkeypatch.setattr(
        materializer,
        "materialize",
        lambda _task, _content: MaterializedDeliverable(
            path=symlink_path,
            content="Managed delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )

    report = CompletionService(
        deliverable_materializer=materializer,
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts == []
    assert symlink_path.is_symlink()
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


@pytest.mark.parametrize("symlink_level", ["task", "execution"])
def test_file_delivery_does_not_materialize_through_managed_directory_symlink(
    tmp_path: Path,
    symlink_level: str,
) -> None:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    task = _task(contract=_file_contract())
    task_dir = output_root / task.id
    if symlink_level == "task":
        target_dir = output_root / "other_task"
        target_dir.mkdir()
        task_dir.symlink_to(target_dir, target_is_directory=True)
        cross_directory_path = target_dir / task.active_execution_id / "delivery.md"
    else:
        task_dir.mkdir()
        target_dir = task_dir / "other_execution"
        target_dir.mkdir()
        (task_dir / task.active_execution_id).symlink_to(
            target_dir,
            target_is_directory=True,
        )
        cross_directory_path = target_dir / "delivery.md"

    report = CompletionService(
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.artifact_ids == []
    assert task.artifacts == []
    assert not cross_directory_path.exists()
    assert "managed final delivery location is invalid" in report.evidence_summary.lower()


def test_file_delivery_revalidation_accepts_active_task_execution_directory(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    task = _task(contract=_file_contract())
    delivery_path = (
        output_root / task.id / task.active_execution_id / "delivery.md"
    )
    delivery_path.parent.mkdir(parents=True)
    delivery_path.write_text("Managed delivery", encoding="utf-8")
    artifact_service = ArtifactService()
    artifact = artifact_service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="Managed delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(output_root),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=[_passed_criterion()],
        artifact_ids=[artifact.id],
        human_accepted=True,
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert report.artifact_ids == [artifact.id]
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.VALID


def test_file_delivery_evaluator_prefers_valid_file_write_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract(with_deliverable_requirements=True))
    artifact_service = ArtifactService()
    tool_path = tmp_path / "delivery.md"
    tool_path.write_text("Tool file", encoding="utf-8")
    tool_artifact = artifact_service.register_tool_result(
        task,
        SubTask(id="subtask_tool", title="Tool", description="Tool output"),
        ToolExecutionResult(
            tool_execution_id="tool_file",
            tool_name="write_file",
            tool_type="file_write",
            success=True,
            result=str(tool_path),
        ),
    )
    text_artifact = artifact_service.register_subtask_output(
        task,
        SubTask(id="subtask_text", title="Text", description="Text output"),
        "Text output",
    )
    assert tool_artifact is not None and text_artifact is not None
    evaluated_artifacts: list[Artifact] = []

    def evaluate(_task: Task, artifacts: list[Artifact]) -> list[DeliverableResult]:
        evaluated_artifacts.extend(artifacts)
        return [
            DeliverableResult(
                requirement_id=requirement.id,
                status=CriterionResultStatus.PASSED,
                artifact_ids=[artifacts[0].id],
                reason="Tool-written delivery contains required content",
            )
            for requirement in _task.contract.deliverable_requirements
        ]

    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        evaluate,
    )
    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(tmp_path / "outputs"),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert len(evaluated_artifacts) == 1
    assert evaluated_artifacts[0].kind == ArtifactKind.FILE
    assert evaluated_artifacts[0].source_type == ArtifactSourceType.TOOL_RESULT
    assert evaluated_artifacts[0].metadata["tool_type"] == "file_write"
    assert not any(
        artifact.metadata.get("managed_final_delivery") is True
        for artifact in task.artifacts
    )


def test_file_delivery_falls_back_to_managed_file_when_tool_filename_mismatches(
    tmp_path: Path,
) -> None:
    task = _task(contract=_file_contract())
    artifact_service = ArtifactService()
    tool_path = tmp_path / "wrong-name.md"
    tool_path.write_text("Tool file", encoding="utf-8")
    tool_artifact = artifact_service.register_tool_result(
        task,
        SubTask(id="subtask_tool", title="Tool", description="Tool output"),
        ToolExecutionResult(
            tool_execution_id="tool_file",
            tool_name="write_file",
            tool_type="file_write",
            success=True,
            result=str(tool_path),
        ),
    )
    assert tool_artifact is not None

    report = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=DeliverableMaterializer(tmp_path / "outputs"),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert any(
        artifact.metadata.get("managed_final_delivery") is True
        and artifact.name == "delivery.md"
        for artifact in task.artifacts
    )


def test_file_delivery_existing_file_does_not_auto_satisfy_content_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract(with_deliverable_requirements=True))
    monkeypatch.setattr(
        "app.services.completion_service.evaluate_deliverable_requirements_with_model",
        lambda _task, _artifacts: None,
    )

    report = CompletionService(
        deliverable_materializer=DeliverableMaterializer(tmp_path),
    ).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.RUNNING
    assert report.awaiting_human_decision is True
    assert all(
        result.status == CriterionResultStatus.PENDING
        for result in report.deliverable_results
    )


def test_text_delivery_keeps_legacy_artifact_behavior_without_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    monkeypatch.setattr(
        materializer,
        "materialize",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("text delivery must not be materialized")
        ),
    )
    task = _task(contract=_contract())

    report = CompletionService(deliverable_materializer=materializer).finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Text delivery",
        reason="done",
        criterion_results=[_passed_criterion()],
    )

    assert report.terminal_status == TaskStatus.SUCCEEDED
    assert len(task.artifacts) == 1
    assert task.artifacts[0].kind == ArtifactKind.TEXT
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


@pytest.mark.parametrize("materialization_error", [OSError, ValueError])
def test_file_delivery_human_acceptance_reuses_existing_managed_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    materialization_error: type[Exception],
) -> None:
    task = _task(contract=_file_contract(requires_human_acceptance=True))
    task.context.summary = "Accepted delivery"
    materializer = DeliverableMaterializer(tmp_path)
    service = CompletionService(deliverable_materializer=materializer)

    pending_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )
    pending_artifact = task.artifacts[0].model_copy(deep=True)
    delivery_path = Path(pending_artifact.uri.removeprefix("file://"))

    def fail_materialization(_task: Task, _content: str) -> MaterializedDeliverable:
        raise materialization_error("sensitive materialization failure")

    monkeypatch.setattr(materializer, "materialize", fail_materialization)
    accepted_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=pending_report.criterion_results,
        artifact_ids=pending_report.artifact_ids,
        human_accepted=True,
        human_override=True,
        decided_by_type="human",
        decided_by_id="user_1",
    )

    assert pending_report.terminal_status == TaskStatus.RUNNING
    assert accepted_report.terminal_status == TaskStatus.SUCCEEDED
    assert accepted_report.artifact_ids == pending_report.artifact_ids
    assert len(task.artifacts) == 1
    accepted_artifact = task.artifacts[0]
    assert accepted_artifact.id == pending_artifact.id
    assert accepted_artifact.created_at == pending_artifact.created_at
    assert accepted_artifact.content == pending_artifact.content
    assert accepted_artifact.checksum == pending_artifact.checksum
    assert delivery_path.read_text(encoding="utf-8") == "Workflow output"


def test_file_delivery_human_acceptance_revalidates_reused_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract(requires_human_acceptance=True))
    task.context.summary = "Accepted delivery"
    materializer = DeliverableMaterializer(tmp_path)
    service = CompletionService(deliverable_materializer=materializer)
    pending_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )
    artifact = task.artifacts[0]
    delivery_path = Path(artifact.uri.removeprefix("file://"))
    delivery_path.write_text("Tampered delivery", encoding="utf-8")
    monkeypatch.setattr(
        materializer,
        "materialize",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("human acceptance must reuse the existing file")
        ),
    )

    accepted_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=pending_report.criterion_results,
        artifact_ids=pending_report.artifact_ids,
        human_accepted=True,
        human_override=True,
        decided_by_type="human",
        decided_by_id="user_1",
    )

    assert accepted_report.terminal_status == TaskStatus.RUNNING
    assert accepted_report.awaiting_human_decision is True
    assert accepted_report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert "checksum" in accepted_report.evidence_summary.lower()


def test_file_delivery_human_acceptance_does_not_replace_preinvalidated_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(contract=_file_contract(requires_human_acceptance=True))
    materializer = DeliverableMaterializer(tmp_path)
    artifact_service = ArtifactService()
    service = CompletionService(
        artifact_service=artifact_service,
        deliverable_materializer=materializer,
    )
    pending_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="done",
        criterion_results=[_passed_criterion()],
    )
    artifact = task.artifacts[0]
    delivery_path = Path(artifact.uri.removeprefix("file://"))
    delivery_path.write_text("Tampered delivery", encoding="utf-8")
    artifact_service.revalidate(task, artifact)
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    monkeypatch.setattr(
        materializer,
        "materialize",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("human acceptance must not replace invalid evidence")
        ),
    )

    accepted_report = service.finalize(
        task,
        candidate_status=TaskStatus.SUCCEEDED,
        output="Workflow output",
        reason="accepted",
        criterion_results=pending_report.criterion_results,
        artifact_ids=pending_report.artifact_ids,
        human_accepted=True,
        decided_by_type="human",
        decided_by_id="user_1",
    )

    assert accepted_report.terminal_status == TaskStatus.RUNNING
    assert accepted_report.artifact_ids == []
    assert task.artifacts[0].validation_status == ArtifactValidationStatus.INVALID
    assert delivery_path.read_text(encoding="utf-8") == "Tampered delivery"
