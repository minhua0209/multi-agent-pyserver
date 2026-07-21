from __future__ import annotations

from app.core.config import require_system_mock_fallback_enabled
from app.core.enums import ArtifactValidationStatus, CriterionResultStatus, CurrentNode, TaskStatus, TaskType
from app.core.model_client import (
    evaluate_deliverable_requirements_with_model,
    evaluate_success_criteria_with_model,
)
from app.core.models import (
    Artifact,
    CompletionReport,
    CriterionResult,
    DeliverableResult,
    Task,
    new_id,
    utc_now,
)
from app.services.artifact_service import ArtifactService
from app.services.execution_service import ExecutionService


class CompletionService:
    _non_success_statuses = {
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.PARTIAL,
        TaskStatus.CANCELLED,
    }
    _blocking_subtask_statuses = {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.PARTIAL,
    }

    def __init__(
        self,
        execution_service: ExecutionService | None = None,
        artifact_service: ArtifactService | None = None,
    ) -> None:
        self.execution_service = execution_service or ExecutionService()
        self.artifact_service = artifact_service or ArtifactService()

    def finalize(
        self,
        task: Task,
        *,
        candidate_status: TaskStatus,
        output: str,
        reason: str,
        criterion_results: list[CriterionResult] | None = None,
        artifact_ids: list[str] | None = None,
        workflow_end_reached: bool = False,
        workflow_end_node_id: str | None = None,
        human_accepted: bool = False,
        human_override: bool = False,
        decided_by_type: str = "system",
        decided_by_id: str = "",
    ) -> CompletionReport:
        normalized_output = output.strip()
        normalized_reason = reason.strip() or self._default_reason(candidate_status)
        normalized_results = [result.model_copy(deep=True) for result in criterion_results or []]
        if normalized_output and task.active_execution_id is not None:
            self.artifact_service.register_task_output(task, normalized_output)
        current_artifacts = self.artifact_service.current(task)
        requested_artifact_ids = (
            [artifact.id for artifact in current_artifacts]
            if artifact_ids is None
            else list(dict.fromkeys(artifact_ids))
        )
        resolved_artifacts = self.artifact_service.resolve(task, requested_artifact_ids)
        resolved_ids = {artifact.id for artifact in resolved_artifacts}
        unknown_artifact_ids = [
            artifact_id
            for artifact_id in requested_artifact_ids
            if artifact_id not in resolved_ids
        ]
        for artifact in resolved_artifacts:
            self.artifact_service.revalidate(task, artifact)
        resolved_artifacts = self.artifact_service.resolve(task, requested_artifact_ids)
        non_valid_artifacts = [
            artifact
            for artifact in resolved_artifacts
            if artifact.validation_status != ArtifactValidationStatus.VALID
        ]
        selected_artifacts = [
            artifact
            for artifact in resolved_artifacts
            if artifact.validation_status == ArtifactValidationStatus.VALID
        ]
        selected_artifact_ids = {artifact.id for artifact in selected_artifacts}
        artifact_gaps = [
            f"artifact {artifact_id} is unknown or does not belong to the active execution"
            for artifact_id in unknown_artifact_ids
        ]
        artifact_gaps.extend(
            f"artifact {artifact.id} is {artifact.validation_status.value}: {artifact.validation_reason}"
            for artifact in non_valid_artifacts
        )
        for result in normalized_results:
            invalid_evidence_ids = [
                artifact_id
                for artifact_id in result.evidence_artifact_ids
                if artifact_id not in selected_artifact_ids
            ]
            artifact_gaps.extend(
                f"criterion {result.criterion_id} evidence artifact {artifact_id} is not selected and valid"
                for artifact_id in invalid_evidence_ids
            )
            result.evidence_artifact_ids = [
                artifact_id
                for artifact_id in result.evidence_artifact_ids
                if artifact_id in selected_artifact_ids
            ]
        effective_human_acceptance = human_accepted
        terminal_status = candidate_status
        deliverable_results: list[DeliverableResult] = []
        gaps: list[str] = []
        awaiting_human_acceptance = False
        awaiting_human_decision = False

        if candidate_status == TaskStatus.SUCCEEDED:
            if not human_override:
                deliverable_results, selected_artifacts = self._evaluate_deliverables(
                    task,
                    selected_artifacts,
                )
                normalized_results, gaps = self._evaluate_success(
                    task,
                    normalized_output,
                    normalized_results,
                    deliverable_results,
                    selected_artifacts,
                    artifact_gaps,
                    workflow_end_reached,
                )
            if gaps:
                if self._requires_human_acceptance(task, effective_human_acceptance):
                    gaps.append("human acceptance is required")
                awaiting_human_decision = True
                terminal_status = TaskStatus.RUNNING
                normalized_reason = f"Awaiting human adjudication: {'; '.join(gaps)}"
            elif self._requires_human_acceptance(task, effective_human_acceptance):
                awaiting_human_acceptance = True
                terminal_status = TaskStatus.RUNNING
                normalized_reason = "Awaiting required human acceptance"
                gaps.append("human acceptance is required")
        elif candidate_status == TaskStatus.BLOCKED:
            awaiting_human_decision = True
            terminal_status = TaskStatus.RUNNING
            gaps.append(normalized_reason)
            normalized_reason = f"Awaiting human adjudication: {normalized_reason}"
        elif candidate_status not in self._non_success_statuses:
            terminal_status = TaskStatus.BLOCKED
            gaps.append(f"unsupported terminal status: {candidate_status.value}")
            normalized_reason = f"Completion blocked: {gaps[0]}"

        active_execution = self.execution_service.active(task)
        report = CompletionReport(
            id=new_id("completion"),
            execution_id=active_execution.id if active_execution else "",
            terminal_status=terminal_status,
            completion_reason=normalized_reason,
            criterion_results=normalized_results,
            deliverable_results=deliverable_results,
            artifact_ids=[artifact.id for artifact in selected_artifacts],
            workflow_end_node_id=workflow_end_node_id if workflow_end_reached else None,
            human_accepted=effective_human_acceptance,
            awaiting_human_decision=awaiting_human_decision,
            automatic_gaps=list(gaps) if awaiting_human_decision else [],
            decided_by_type=decided_by_type,
            decided_by_id=decided_by_id,
            decided_at=utc_now(),
            evidence_summary="; ".join(gaps) if gaps else self._evidence_summary(normalized_results),
        )
        task.task_status = terminal_status
        task.current_node = (
            CurrentNode.HUMAN_INTERVENTION
            if awaiting_human_acceptance or awaiting_human_decision
            else CurrentNode.COMPLETION_JUDGE
        )
        task.final_output = normalized_output
        task.completion_report = report
        task.updated_at = report.decided_at
        self.execution_service.sync_projection(task)
        return report

    def evaluate_criteria(self, task: Task, output: str) -> list[CriterionResult]:
        if task.contract is None or task.contract.legacy_inferred:
            return []
        results = evaluate_success_criteria_with_model(task, output)
        if results is not None:
            return results
        require_system_mock_fallback_enabled("criterion_evaluation")
        normalized_output = output.strip()
        return [
            CriterionResult(
                criterion_id=criterion.id,
                status=(
                    CriterionResultStatus.PASSED
                    if normalized_output
                    else CriterionResultStatus.PENDING
                ),
                evidence_text=normalized_output,
                reason="System mock fallback inferred criterion result from task output",
            )
            for criterion in task.contract.success_criteria
        ]

    def _evaluate_success(
        self,
        task: Task,
        output: str,
        criterion_results: list[CriterionResult],
        deliverable_results: list[DeliverableResult],
        selected_artifacts: list[Artifact],
        artifact_gaps: list[str],
        workflow_end_reached: bool,
    ) -> tuple[list[CriterionResult], list[str]]:
        gaps: list[str] = list(artifact_gaps)
        if not output:
            gaps.append("output is empty")
        if not selected_artifacts:
            gaps.append("at least one valid current execution artifact is required")

        blocking_subtasks = [
            subtask
            for round_item in task.context.rounds
            for subtask in round_item.subtasks
            if subtask.status in self._blocking_subtask_statuses
        ]
        if blocking_subtasks:
            statuses = ", ".join(sorted({subtask.status.value for subtask in blocking_subtasks}))
            gaps.append(f"subtasks remain in blocking statuses: {statuses}")

        if task.task_type == TaskType.MANUAL_ORCHESTRATION and not workflow_end_reached:
            gaps.append("workflow end was not reached")

        contract = task.contract
        if contract is None:
            gaps.append("task contract is missing")
        elif contract.legacy_inferred:
            criterion_results = self._legacy_results(contract.success_criteria, criterion_results, output)
            gaps.extend(
                f"criterion {result.criterion_id} has explicit {result.status.value} evidence"
                for result in criterion_results
                if result.status != CriterionResultStatus.PASSED
            )
        else:
            criterion_results, missing_criteria = self._explicit_results(
                contract.success_criteria,
                criterion_results,
            )
            gaps.extend(f"criterion {criterion_id} has no passed evidence" for criterion_id in missing_criteria)

        gaps.extend(
            f"deliverable requirement {result.requirement_id} is {result.status.value}: {result.reason}"
            for result in deliverable_results
            if result.status != CriterionResultStatus.PASSED
        )
        return criterion_results, gaps

    @staticmethod
    def _requires_human_acceptance(task: Task, human_accepted: bool) -> bool:
        return bool(
            task.contract is not None
            and task.contract.requires_human_acceptance
            and not human_accepted
        )

    def _evaluate_deliverables(
        self,
        task: Task,
        selected_artifacts: list[Artifact],
    ) -> tuple[list[DeliverableResult], list[Artifact]]:
        contract = task.contract
        if contract is None or not contract.deliverable_requirements:
            return [], selected_artifacts

        results: list[DeliverableResult] = []
        uncovered_requirements = []
        for requirement in contract.deliverable_requirements:
            artifact_ids = [
                artifact.id
                for artifact in selected_artifacts
                if requirement.id in artifact.deliverable_requirement_ids
            ]
            if artifact_ids:
                results.append(
                    DeliverableResult(
                        requirement_id=requirement.id,
                        status=CriterionResultStatus.PASSED,
                        artifact_ids=artifact_ids,
                        reason="Covered by explicit artifact mapping",
                    )
                )
            else:
                uncovered_requirements.append(requirement)

        evaluated_by_id: dict[str, DeliverableResult] = {}
        if uncovered_requirements:
            evaluation_contract = contract.model_copy(
                update={"deliverable_requirements": uncovered_requirements},
                deep=True,
            )
            evaluation_task = task.model_copy(
                update={"contract": evaluation_contract},
                deep=True,
            )
            evaluated = evaluate_deliverable_requirements_with_model(
                evaluation_task,
                selected_artifacts,
            )
            if evaluated is None:
                evaluated = [
                    DeliverableResult(
                        requirement_id=requirement.id,
                        status=CriterionResultStatus.PENDING,
                        reason="Deliverable evaluator unavailable",
                    )
                    for requirement in uncovered_requirements
                ]
            evaluated_by_id = {
                result.requirement_id: result
                for result in evaluated
                if result.requirement_id in {item.id for item in uncovered_requirements}
            }

        explicit_by_id = {result.requirement_id: result for result in results}
        selected_ids = {artifact.id for artifact in selected_artifacts}
        normalized_results: list[DeliverableResult] = []
        requirement_updates: dict[str, list[str]] = {}
        for requirement in contract.deliverable_requirements:
            result = explicit_by_id.get(requirement.id) or evaluated_by_id.get(requirement.id)
            if result is None:
                result = DeliverableResult(
                    requirement_id=requirement.id,
                    status=CriterionResultStatus.PENDING,
                    reason="Deliverable evaluation did not return this requirement",
                )
            elif any(
                artifact_id not in selected_ids for artifact_id in result.artifact_ids
            ):
                result = result.model_copy(
                    update={
                        "status": CriterionResultStatus.PENDING,
                        "artifact_ids": [],
                        "reason": "Deliverable result references artifacts outside the selected set",
                    }
                )
            elif result.status == CriterionResultStatus.PASSED and not result.artifact_ids:
                result = result.model_copy(
                    update={
                        "status": CriterionResultStatus.PENDING,
                        "reason": "Passed deliverable result must reference selected artifacts",
                    }
                )
            normalized_results.append(result)
            if result.status == CriterionResultStatus.PASSED:
                for artifact_id in result.artifact_ids:
                    requirement_updates.setdefault(artifact_id, []).append(requirement.id)

        for artifact in selected_artifacts:
            added_requirement_ids = requirement_updates.get(artifact.id, [])
            if not added_requirement_ids:
                continue
            updated_requirement_ids = list(
                dict.fromkeys(
                    artifact.deliverable_requirement_ids + added_requirement_ids
                )
            )
            if updated_requirement_ids == artifact.deliverable_requirement_ids:
                continue
            self.artifact_service.replace_current(
                task,
                artifact.model_copy(
                    update={"deliverable_requirement_ids": updated_requirement_ids}
                ),
            )
        return normalized_results, self.artifact_service.resolve(
            task,
            [artifact.id for artifact in selected_artifacts],
        )

    @staticmethod
    def _explicit_results(criteria, results: list[CriterionResult]) -> tuple[list[CriterionResult], list[str]]:
        by_id = {result.criterion_id: result for result in results}
        normalized: list[CriterionResult] = []
        missing: list[str] = []
        for criterion in criteria:
            result = by_id.get(criterion.id)
            if result is None:
                result = CriterionResult(
                    criterion_id=criterion.id,
                    status=CriterionResultStatus.PENDING,
                    reason="Missing passed criterion evidence",
                )
            normalized.append(result)
            if result.status != CriterionResultStatus.PASSED:
                missing.append(criterion.id)
        return normalized, missing

    @staticmethod
    def _legacy_results(criteria, results: list[CriterionResult], output: str) -> list[CriterionResult]:
        by_id = {result.criterion_id: result for result in results}
        normalized = []
        for criterion in criteria:
            explicit_result = by_id.get(criterion.id)
            if explicit_result is not None:
                normalized.append(explicit_result)
                continue
            normalized.append(
                CriterionResult(
                    criterion_id=criterion.id,
                    status=CriterionResultStatus.PASSED if output else CriterionResultStatus.PENDING,
                    evidence_text=output,
                    reason="Inferred from legacy task output" if output else "Legacy task output is empty",
                )
            )
        return normalized

    @staticmethod
    def _evidence_summary(results: list[CriterionResult]) -> str:
        passed = sum(result.status == CriterionResultStatus.PASSED for result in results)
        return f"{passed}/{len(results)} success criteria passed" if results else "No criterion evidence required"

    @staticmethod
    def _default_reason(status: TaskStatus) -> str:
        return f"Task finalized as {status.value}"
