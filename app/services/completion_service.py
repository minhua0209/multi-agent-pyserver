from __future__ import annotations

import hashlib

from app.core.config import require_system_mock_fallback_enabled
from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CriterionResultStatus,
    CurrentNode,
    TaskStatus,
    TaskType,
)
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
from app.services.deliverable_materializer import (
    DeliverableMaterializer,
    ManagedDeliveryPathError,
)
from app.services.execution_service import ExecutionService
from app.services import file_uri


class CompletionService:
    _invalid_managed_filename_reason = (
        "Managed final delivery filename is invalid"
    )
    _invalid_managed_location_reason = (
        "Managed final delivery location is invalid"
    )
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
        deliverable_materializer: DeliverableMaterializer | None = None,
    ) -> None:
        self.execution_service = execution_service or ExecutionService()
        self.artifact_service = artifact_service or ArtifactService()
        self.deliverable_materializer = (
            deliverable_materializer or DeliverableMaterializer()
        )

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
        criterion_results_supplied = criterion_results is not None
        normalized_output = (
            self.delivery_content(task, output)
            if candidate_status == TaskStatus.SUCCEEDED
            else output.strip()
        )
        normalized_reason = reason.strip() or self._default_reason(candidate_status)
        normalized_results = [result.model_copy(deep=True) for result in criterion_results or []]
        artifact_gaps: list[str] = []
        blocking_gaps: list[str] = []
        reuse_managed_file = bool(
            human_accepted
            and any(
                self._is_managed_file_candidate(task, artifact)
                for artifact in self.artifact_service.current(task)
            )
        )
        reuse_tool_file = bool(
            candidate_status == TaskStatus.SUCCEEDED
            and self._is_file_delivery(task)
            and self._has_valid_tool_file_delivery(task)
        )
        if candidate_status == TaskStatus.SUCCEEDED and self._is_file_delivery(task):
            if not reuse_managed_file and not reuse_tool_file:
                try:
                    materialized = self.deliverable_materializer.materialize(
                        task,
                        normalized_output,
                    )
                    try:
                        content_bytes = (
                            self.deliverable_materializer.read_managed_delivery(
                                task,
                                materialized.path.as_uri(),
                            )
                        )
                    except ValueError:
                        self.artifact_service.register_task_file_output(
                            task,
                            materialized,
                        )
                    except OSError:
                        artifact_gaps.append(
                            self._invalid_managed_location_reason
                        )
                    else:
                        if content_bytes != materialized.content.encode("utf-8"):
                            artifact_gaps.append(
                                "managed final delivery checksum does not match its content snapshot"
                            )
                        else:
                            self.artifact_service.register_task_file_output(
                                task,
                                materialized,
                            )
                except ManagedDeliveryPathError:
                    artifact_gaps.append(self._invalid_managed_location_reason)
                except ValueError:
                    artifact_gaps.append(
                        "managed final delivery materialization was rejected"
                    )
                except OSError:
                    artifact_gaps.append(
                        "managed final delivery could not be written"
                    )
        elif normalized_output and task.active_execution_id is not None:
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
            if self._is_managed_file_candidate(task, artifact):
                if not self._managed_file_location_is_valid(task, artifact.uri):
                    self._invalidate_managed_file(
                        task,
                        artifact,
                        self._invalid_managed_location_reason,
                    )
                elif not self._managed_file_uri_name_is_valid(task, artifact):
                    self._revalidate_managed_file_name(task, artifact)
                elif artifact.validation_status != ArtifactValidationStatus.INVALID:
                    revalidated = self._revalidate_managed_file(task, artifact)
                    if (
                        revalidated.validation_status
                        == ArtifactValidationStatus.VALID
                        and not self._managed_file_name_is_valid(task, revalidated)
                    ):
                        self._revalidate_managed_file_name(task, revalidated)
                continue
            self.artifact_service.revalidate(task, artifact)
        resolved_artifacts = self.artifact_service.resolve(task, requested_artifact_ids)
        file_delivery_gaps: list[str] = []
        if candidate_status == TaskStatus.SUCCEEDED and self._is_file_delivery(task):
            file_delivery_gaps = self._file_delivery_gaps(
                task,
                [
                    artifact
                    for artifact in resolved_artifacts
                    if artifact.validation_status == ArtifactValidationStatus.VALID
                ],
            )
            resolved_artifacts = self.artifact_service.resolve(
                task,
                requested_artifact_ids,
            )
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
        if (
            candidate_status == TaskStatus.SUCCEEDED
            and not human_override
            and not criterion_results_supplied
        ):
            normalized_results = self.evaluate_criteria(task, normalized_output)
        artifact_gaps.extend(
            f"artifact {artifact_id} is unknown or does not belong to the active execution"
            for artifact_id in unknown_artifact_ids
        )
        for artifact in non_valid_artifacts:
            gap = (
                f"artifact {artifact.id} is {artifact.validation_status.value}: "
                f"{artifact.validation_reason}"
            )
            artifact_gaps.append(gap)
            if self._is_managed_file_candidate(task, artifact):
                blocking_gaps.append(gap)
        artifact_gaps.extend(file_delivery_gaps)
        blocking_gaps.extend(file_delivery_gaps)
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
            if blocking_gaps:
                gaps = list(dict.fromkeys([*gaps, *blocking_gaps]))
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

    @staticmethod
    def delivery_content(task: Task, output: str) -> str:
        normalized_output = output.strip()
        if (
            CompletionService._is_file_delivery(task)
            and CompletionService._has_completed_round_content(task)
        ):
            return task.context.summary.strip() or normalized_output
        return normalized_output

    @staticmethod
    def _has_completed_round_content(task: Task) -> bool:
        subtasks = [
            subtask
            for round_item in task.context.rounds
            for subtask in round_item.subtasks
        ]
        if any(subtask.status == TaskStatus.RUNNING for subtask in subtasks):
            return False
        return any(
            subtask.status == TaskStatus.SUCCEEDED and subtask.output.strip()
            for subtask in subtasks
        )

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

        deliverable_artifacts = (
            self._delivery_file_artifacts(task, selected_artifacts)
            if self._is_file_delivery(task)
            else selected_artifacts
        )

        results: list[DeliverableResult] = []
        uncovered_requirements = []
        for requirement in contract.deliverable_requirements:
            artifact_ids = [
                artifact.id
                for artifact in deliverable_artifacts
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
                deliverable_artifacts,
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
        selected_ids = {artifact.id for artifact in deliverable_artifacts}
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

        for artifact in deliverable_artifacts:
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

    def _file_delivery_gaps(
        self,
        task: Task,
        selected_artifacts: list[Artifact],
    ) -> list[str]:
        delivery_files = self._delivery_file_artifacts(task, selected_artifacts)
        if not delivery_files:
            return ["a valid final delivery file must be selected"]

        contract = task.contract
        if contract is None:
            return ["final delivery contract is missing"]
        try:
            expected_filename = self.deliverable_materializer.expected_filename(task)
        except ValueError:
            return [self._invalid_managed_filename_reason]
        expected_extension = (
            ".md" if contract.deliverable_format == "markdown" else ".txt"
        )
        expected_media_type = (
            "text/markdown"
            if contract.deliverable_format == "markdown"
            else "text/plain"
        )
        gaps: list[str] = []
        for artifact in delivery_files:
            artifact_gaps: list[str] = []
            if (
                not artifact.name.lower().endswith(expected_extension)
                or (
                    self._is_managed_file_candidate(task, artifact)
                    and artifact.metadata.get("deliverable_format")
                    != contract.deliverable_format
                )
            ):
                artifact_gaps.append(
                    "final delivery format does not match the task contract"
                )
            if artifact.media_type != expected_media_type:
                artifact_gaps.append(
                    "final delivery media type does not match the task contract"
                )
            if artifact.name != expected_filename:
                artifact_gaps.append(self._invalid_managed_filename_reason)
            try:
                if self._is_managed_file_candidate(task, artifact):
                    content_bytes = self.deliverable_materializer.read_managed_delivery(
                        task,
                        artifact.uri,
                    )
                else:
                    path = file_uri.local_file_uri_to_path(artifact.uri)
                    if path is None:
                        raise ValueError("file artifact URI is not local")
                    content_bytes = path.read_bytes()
            except ValueError:
                artifact_gaps.append("final delivery location is invalid")
                if self._is_managed_file_candidate(task, artifact):
                    self._invalidate_managed_file(
                        task,
                        artifact,
                        self._invalid_managed_location_reason,
                    )
            except OSError:
                artifact_gaps.append("final delivery file could not be read")
                if self._is_managed_file_candidate(task, artifact):
                    self._invalidate_managed_file(
                        task,
                        artifact,
                        self._invalid_managed_location_reason,
                    )
            else:
                if not content_bytes:
                    artifact_gaps.append("final delivery file is empty")
                file_checksum = self._bytes_checksum(content_bytes)
                if not artifact.checksum or artifact.checksum != file_checksum:
                    artifact_gaps.append(
                        "final delivery checksum does not match the file"
                    )
                if (
                    self._is_managed_file_candidate(task, artifact)
                    and (
                        not artifact.content
                        or self._bytes_checksum(artifact.content.encode("utf-8"))
                        != file_checksum
                    )
                ):
                    artifact_gaps.append(
                        "managed final delivery checksum does not match its content snapshot"
                    )
                if artifact_gaps and self._is_managed_file_candidate(task, artifact):
                    self._invalidate_managed_file(
                        task,
                        artifact,
                        artifact_gaps[0],
                    )
            if not artifact_gaps:
                return []
            gaps.extend(artifact_gaps)
        return gaps

    def _has_valid_tool_file_delivery(self, task: Task) -> bool:
        for artifact in list(self.artifact_service.current(task)):
            if not self._is_tool_file_candidate(task, artifact):
                continue
            revalidated = self.artifact_service.revalidate(task, artifact)
            if (
                revalidated.validation_status == ArtifactValidationStatus.VALID
                and not self._file_delivery_gaps(task, [revalidated])
            ):
                return True
        return False

    def _revalidate_managed_file(
        self,
        task: Task,
        artifact: Artifact,
    ) -> Artifact:
        if not self._is_managed_file_candidate(task, artifact):
            return artifact
        try:
            content_bytes = self.deliverable_materializer.read_managed_delivery(
                task,
                artifact.uri,
            )
        except (OSError, ValueError):
            return self._invalidate_managed_file(
                task,
                artifact,
                self._invalid_managed_location_reason,
            )

        if not content_bytes:
            return self._invalidate_managed_file(
                task,
                artifact,
                "Managed final delivery file is empty",
            )
        file_checksum = self._bytes_checksum(content_bytes)
        if not artifact.checksum or artifact.checksum != file_checksum:
            return self._invalidate_managed_file(
                task,
                artifact,
                "Managed final delivery checksum does not match registration",
            )
        try:
            snapshot_checksum = self._bytes_checksum(artifact.content.encode("utf-8"))
        except UnicodeEncodeError:
            snapshot_checksum = ""
        if not artifact.content or snapshot_checksum != file_checksum:
            return self._invalidate_managed_file(
                task,
                artifact,
                "Managed final delivery content snapshot does not match the file",
            )
        if artifact.validation_status == ArtifactValidationStatus.VALID:
            return artifact
        return self.artifact_service.replace_current(
            task,
            artifact.model_copy(
                update={
                    "validation_status": ArtifactValidationStatus.VALID,
                    "validation_reason": (
                        "Managed final delivery exists and checksum matches registration"
                    ),
                }
            ),
        )

    def _invalidate_managed_file(
        self,
        task: Task,
        artifact: Artifact,
        reason: str,
    ) -> Artifact:
        if (
            artifact.validation_status == ArtifactValidationStatus.INVALID
            and artifact.validation_reason == reason
        ):
            return artifact
        return self.artifact_service.replace_current(
            task,
            artifact.model_copy(
                update={
                    "validation_status": ArtifactValidationStatus.INVALID,
                    "validation_reason": reason,
                }
            ),
        )

    def _revalidate_managed_file_name(
        self,
        task: Task,
        artifact: Artifact,
    ) -> Artifact:
        if (
            not self._is_managed_file_candidate(task, artifact)
            or self._managed_file_name_is_valid(task, artifact)
        ):
            return artifact
        return self.artifact_service.replace_current(
            task,
            artifact.model_copy(
                update={
                    "validation_status": ArtifactValidationStatus.INVALID,
                    "validation_reason": self._invalid_managed_filename_reason,
                }
            ),
        )

    def _managed_file_name_is_valid(
        self,
        task: Task,
        artifact: Artifact,
    ) -> bool:
        path = file_uri.local_file_uri_to_path(artifact.uri)
        if path is None:
            return False
        try:
            expected_filename = self.deliverable_materializer.expected_filename(task)
        except ValueError:
            return False
        return path.name == expected_filename and artifact.name == path.name

    def _managed_file_uri_name_is_valid(
        self,
        task: Task,
        artifact: Artifact,
    ) -> bool:
        path = file_uri.local_file_uri_to_path(artifact.uri)
        if path is None:
            return False
        try:
            expected_filename = self.deliverable_materializer.expected_filename(task)
        except ValueError:
            return False
        return path.name == expected_filename

    def _managed_file_location_is_valid(self, task: Task, uri: str) -> bool:
        path = file_uri.local_file_uri_to_path(uri)
        if path is None or ".." in path.parts:
            return False
        active_execution_id = task.active_execution_id
        if active_execution_id is None:
            return False
        expected_parent = (
            self.deliverable_materializer.output_root
            / task.id
            / active_execution_id
        )
        return path.parent == expected_parent

    @staticmethod
    def _is_managed_file_candidate(task: Task, artifact: Artifact) -> bool:
        return bool(
            artifact.task_id == task.id
            and artifact.execution_id == task.active_execution_id
            and artifact.kind == ArtifactKind.FILE
            and artifact.source_type == ArtifactSourceType.TASK_RESULT
            and artifact.source_id == f"{task.id}:file"
            and artifact.metadata.get("managed_final_delivery") is True
        )

    @staticmethod
    def _managed_file_artifacts(
        task: Task,
        artifacts: list[Artifact],
    ) -> list[Artifact]:
        return [
            artifact
            for artifact in artifacts
            if CompletionService._is_managed_file_candidate(task, artifact)
            and artifact.validation_status == ArtifactValidationStatus.VALID
        ]

    @staticmethod
    def _is_tool_file_candidate(task: Task, artifact: Artifact) -> bool:
        return bool(
            artifact.task_id == task.id
            and artifact.execution_id == task.active_execution_id
            and artifact.kind == ArtifactKind.FILE
            and artifact.source_type == ArtifactSourceType.TOOL_RESULT
            and artifact.metadata.get("tool_type") == "file_write"
        )

    @staticmethod
    def _delivery_file_artifacts(
        task: Task,
        artifacts: list[Artifact],
    ) -> list[Artifact]:
        return [
            artifact
            for artifact in artifacts
            if artifact.validation_status == ArtifactValidationStatus.VALID
            and (
                CompletionService._is_managed_file_candidate(task, artifact)
                or CompletionService._is_tool_file_candidate(task, artifact)
            )
        ]

    @staticmethod
    def _bytes_checksum(content: bytes) -> str:
        return "sha256:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def _is_file_delivery(task: Task) -> bool:
        return bool(
            task.contract is not None
            and task.contract.deliverable_kind == "file"
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
