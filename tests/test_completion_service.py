import pytest
from pydantic import ValidationError
import app.core.models as core_models

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


def test_completion_report_supports_structured_deliverable_results() -> None:
    assert hasattr(core_models, "DeliverableResult")
    assert "deliverable_results" in core_models.CompletionReport.model_fields


def _contract(
    *,
    legacy: bool = False,
    requires_human_acceptance: bool = False,
    with_deliverable_requirements: bool = False,
) -> TaskContract:
    return TaskContract(
        goal="Prepare a delivery plan",
        deliverable_goal="A reviewable plan",
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


def test_explicit_contract_requires_passed_evidence_for_every_criterion() -> None:
    task = _task(contract=_contract())

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
