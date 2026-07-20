from __future__ import annotations

from copy import deepcopy

from app.core.enums import CurrentNode, ExecutionTriggerType, TaskStatus
from app.core.models import (
    RerunIssue,
    RerunSideEffect,
    Task,
    TaskExecution,
    TaskRerunCreate,
    TaskRerunPreflightRequest,
    TaskRerunPreflightResponse,
    User,
    new_id,
    utc_now,
)


class ExecutionProjectionConflictError(RuntimeError):
    pass


class ExecutionNotFoundError(LookupError):
    pass


class TaskRerunNotAllowedError(RuntimeError):
    def __init__(self, preflight: TaskRerunPreflightResponse) -> None:
        self.preflight = preflight
        super().__init__("task rerun preflight rejected the request")


class TaskRerunSideEffectConfirmationRequiredError(RuntimeError):
    def __init__(self, preflight: TaskRerunPreflightResponse) -> None:
        self.preflight = preflight
        super().__init__("task rerun requires side effect confirmation")


class ExecutionService:
    def create_initial(
        self,
        task: Task,
        actor: User,
        start_node: CurrentNode,
        execution_mode: str = "sync",
    ) -> TaskExecution:
        active = self.active(task)
        if active is not None:
            return active

        existing = next(
            (
                execution
                for execution in task.executions
                if execution.attempt_no == 1 and execution.trigger_type == ExecutionTriggerType.INITIAL
            ),
            None,
        )
        if existing is not None:
            task.active_execution_id = existing.id
            return existing

        now = utc_now()
        execution = TaskExecution(
            id=new_id("execution"),
            task_id=task.id,
            attempt_no=1,
            trigger_type=ExecutionTriggerType.INITIAL,
            trigger_reason="Initial task confirmation",
            triggered_by_user_id=actor.id,
            triggered_by_user_name=actor.name,
            contract_snapshot=task.contract.model_copy(deep=True) if task.contract else None,
            workflow_snapshot=deepcopy(task.request_metadata.get("workflow_definition")),
            status=task.task_status,
            start_node=start_node,
            current_node=task.current_node,
            context_snapshot=task.context.model_copy(deep=True),
            artifacts=deepcopy(task.artifacts),
            loop_count=task.loop_count,
            final_output=task.final_output,
            created_at=now,
            started_at=None,
            execution_mode=execution_mode,
            completion_report=deepcopy(task.completion_report),
        )
        task.executions.append(execution)
        task.active_execution_id = execution.id
        return execution

    @staticmethod
    def active(task: Task) -> TaskExecution | None:
        if task.active_execution_id is None:
            return None
        return next(
            (execution for execution in task.executions if execution.id == task.active_execution_id),
            None,
        )

    @staticmethod
    def list(task: Task) -> list[TaskExecution]:
        return task.executions

    @staticmethod
    def get(task: Task, execution_id: str) -> TaskExecution:
        execution = next(
            (item for item in task.executions if item.id == execution_id),
            None,
        )
        if execution is None:
            raise ExecutionNotFoundError(execution_id)
        return execution

    def preflight(
        self,
        task: Task,
        payload: TaskRerunPreflightRequest,
        *,
        dependencies_satisfied: bool = True,
    ) -> TaskRerunPreflightResponse:
        issues: list[RerunIssue] = []
        next_attempt_no = max(
            (execution.attempt_no for execution in task.executions),
            default=0,
        ) + 1
        start_node = (
            CurrentNode.DISPATCH_DECISION
            if dependencies_satisfied
            else CurrentNode.WAITING_DEPENDENCIES
        )
        if task.contract is None:
            issues.append(
                RerunIssue(
                    code="task_contract_missing",
                    message="Task must be confirmed before it can be rerun",
                )
            )
        if task.task_status == TaskStatus.RUNNING:
            issues.append(
                RerunIssue(
                    code="task_not_terminal",
                    message="Task must be terminal before rerun",
                )
            )
        active = self.active(task)
        if active is None:
            issues.append(
                RerunIssue(
                    code="active_execution_missing",
                    message="Task active execution is missing",
                )
            )
        else:
            if active.status == TaskStatus.RUNNING or active.finished_at is None:
                issues.append(
                    RerunIssue(
                        code="active_execution_not_terminal",
                        message="Task active execution must be terminal",
                    )
                )
            projection_checks = [
                (
                    active.status == task.task_status,
                    "active_status_mismatch",
                    "Active execution status does not match task projection",
                ),
                (
                    active.current_node == task.current_node,
                    "active_current_node_mismatch",
                    "Active execution node does not match task projection",
                ),
                (
                    active.context_snapshot == task.context,
                    "active_context_mismatch",
                    "Active execution context does not match task projection",
                ),
                (
                    active.artifacts == task.artifacts,
                    "active_artifacts_mismatch",
                    "Active execution artifacts do not match task projection",
                ),
                (
                    active.loop_count == task.loop_count,
                    "active_loop_count_mismatch",
                    "Active execution loop count does not match task projection",
                ),
                (
                    active.final_output == task.final_output,
                    "active_output_mismatch",
                    "Active execution output does not match task projection",
                ),
                (
                    active.completion_report == task.completion_report,
                    "active_completion_report_mismatch",
                    "Active execution completion report does not match task projection",
                ),
            ]
            issues.extend(
                RerunIssue(code=code, message=message)
                for matches, code, message in projection_checks
                if not matches
            )
        for execution in task.executions:
            if execution.status == TaskStatus.RUNNING:
                if execution.finished_at is not None:
                    issues.append(
                        RerunIssue(
                            code="running_execution_has_finished_at",
                            message=f"Running execution {execution.id} has finished_at",
                        )
                    )
                else:
                    issues.append(
                        RerunIssue(
                            code="task_execution_running",
                            message=f"Execution {execution.id} is still running",
                        )
                    )
            elif execution.finished_at is None:
                issues.append(
                    RerunIssue(
                        code="terminal_execution_missing_finished_at",
                        message=f"Terminal execution {execution.id} has no finished_at",
                    )
                )
        try:
            source = self.get(task, payload.source_execution_id)
        except ExecutionNotFoundError:
            source = None
            issues.append(
                RerunIssue(
                    code="source_execution_not_found",
                    message="Source execution was not found on this task",
                )
            )
        if source is not None and (
            source.status == TaskStatus.RUNNING or source.finished_at is None
        ):
            issues.append(
                RerunIssue(
                    code="source_execution_not_terminal",
                    message="Source execution must be terminal before rerun",
                )
            )
        if source is not None and source.contract_snapshot is None:
            issues.append(
                RerunIssue(
                    code="source_contract_snapshot_missing",
                    message="Source execution has no confirmed contract snapshot",
                )
            )
        side_effects = self._side_effects(source) if source is not None else []
        return TaskRerunPreflightResponse(
            task_id=task.id,
            source_execution_id=payload.source_execution_id,
            next_attempt_no=next_attempt_no,
            dependencies_satisfied=dependencies_satisfied,
            start_node=start_node,
            will_wait_for_dependencies=not dependencies_satisfied,
            allowed=not issues,
            issues=issues,
            side_effects=side_effects,
            requires_side_effect_confirmation=bool(side_effects),
        )

    def create_rerun(
        self,
        task: Task,
        payload: TaskRerunCreate,
        actor: User,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        start_node: CurrentNode,
        dependencies_satisfied: bool | None = None,
        preflight: TaskRerunPreflightResponse | None = None,
    ) -> TaskExecution:
        effective_dependencies_satisfied = (
            dependencies_satisfied
            if dependencies_satisfied is not None
            else start_node != CurrentNode.WAITING_DEPENDENCIES
        )
        if preflight is None:
            preflight = self.preflight(
                task,
                TaskRerunPreflightRequest(
                    source_execution_id=payload.source_execution_id
                ),
                dependencies_satisfied=effective_dependencies_satisfied,
            )
        if not preflight.allowed:
            raise TaskRerunNotAllowedError(preflight)
        if preflight.requires_side_effect_confirmation and not payload.confirm_side_effects:
            raise TaskRerunSideEffectConfirmationRequiredError(preflight)

        source = self.get(task, payload.source_execution_id)
        now = utc_now()
        confirmation_fields = {}
        if preflight.side_effects and payload.confirm_side_effects:
            confirmation_fields = {
                "side_effects_confirmed_by_user_id": actor.id,
                "side_effects_confirmed_by_user_name": actor.name,
                "side_effects_confirmed_at": now,
            }
        execution = TaskExecution(
            id=new_id("execution"),
            task_id=task.id,
            attempt_no=preflight.next_attempt_no,
            trigger_type=ExecutionTriggerType.RERUN,
            trigger_reason=payload.reason,
            triggered_by_user_id=actor.id,
            triggered_by_user_name=actor.name,
            contract_snapshot=source.contract_snapshot.model_copy(deep=True),
            workflow_snapshot=deepcopy(source.workflow_snapshot),
            status=TaskStatus.RUNNING,
            start_node=preflight.start_node,
            current_node=preflight.start_node,
            context_snapshot=task.initial_context.model_copy(deep=True),
            artifacts=[],
            loop_count=0,
            final_output="",
            created_at=now,
            started_at=None,
            finished_at=None,
            parent_execution_id=source.id,
            retry_of_execution_id=source.id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            execution_mode=payload.execution_mode,
            **confirmation_fields,
        )
        candidate = task.model_copy(
            update={
                "contract": source.contract_snapshot.model_copy(deep=True),
                "task_status": TaskStatus.RUNNING,
                "current_node": preflight.start_node,
                "context": task.initial_context.model_copy(deep=True),
                "executions": [*task.executions, execution],
                "active_execution_id": execution.id,
                "artifacts": [],
                "completion_report": None,
                "final_output": "",
                "loop_count": 0,
                "assigned_agent_id": None,
                "updated_at": now,
            },
            deep=True,
        )
        validated = Task.model_validate(candidate.model_dump(mode="python"))
        task.contract = validated.contract
        task.task_status = validated.task_status
        task.current_node = validated.current_node
        task.context = validated.context
        task.executions = validated.executions
        task.active_execution_id = validated.active_execution_id
        task.artifacts = validated.artifacts
        task.completion_report = validated.completion_report
        task.final_output = validated.final_output
        task.loop_count = validated.loop_count
        task.assigned_agent_id = validated.assigned_agent_id
        task.updated_at = validated.updated_at
        return self.get(task, execution.id)

    def mark_started(self, task: Task) -> TaskExecution | None:
        execution = self.active(task)
        if execution is None:
            return None
        if execution.started_at is None and execution.finished_at is None:
            execution.started_at = utc_now()
        return execution

    def sync_projection(self, task: Task) -> TaskExecution | None:
        execution = self.active(task)
        if execution is None:
            return None
        current_artifacts = [
            artifact
            for artifact in task.artifacts
            if artifact.task_id == task.id and artifact.execution_id == execution.id
        ]

        if execution.finished_at is not None:
            projection_matches = (
                execution.status == task.task_status
                and execution.current_node == task.current_node
                and execution.context_snapshot == task.context
                and execution.artifacts == current_artifacts
                and execution.loop_count == task.loop_count
                and execution.final_output == task.final_output
                and execution.completion_report == task.completion_report
            )
            if projection_matches:
                return execution
            raise ExecutionProjectionConflictError(
                f"finished execution {execution.id} cannot be overwritten"
            )

        execution.status = task.task_status
        execution.current_node = task.current_node
        execution.context_snapshot = task.context.model_copy(deep=True)
        execution.artifacts = deepcopy(current_artifacts)
        execution.loop_count = task.loop_count
        execution.final_output = task.final_output
        execution.completion_report = deepcopy(task.completion_report)
        if task.task_status != TaskStatus.RUNNING and execution.finished_at is None:
            execution.finished_at = utc_now()
        return execution

    @staticmethod
    def _side_effects(source: TaskExecution) -> list[RerunSideEffect]:
        side_effects = []
        for round_item in source.context_snapshot.rounds:
            for subtask in round_item.subtasks:
                for result in subtask.tool_results:
                    has_side_effect = (
                        result.side_effect
                        or result.tool_type in {"smtp_email", "file_write"}
                        or (
                            result.tool_type == "http"
                            and not result.side_effect_known
                        )
                    )
                    if not has_side_effect:
                        continue
                    side_effects.append(
                        RerunSideEffect(
                            subtask_id=subtask.id,
                            tool_execution_id=result.tool_execution_id,
                            tool_name=result.tool_name,
                            tool_type=result.tool_type,
                            argument_keys=sorted(
                                {str(key) for key in result.arguments}
                            ),
                            success=result.success,
                        )
                    )
        return side_effects
