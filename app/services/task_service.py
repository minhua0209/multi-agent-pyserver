import hashlib
import json
from threading import RLock, Thread

from app.core.enums import CurrentNode, ResultStatus, TaskStatus, TaskType, UserRole
from app.core.config import require_system_mock_fallback_enabled
from app.core.model_client import recognize_tasks_with_model
from app.core.mock_llm import (
    mock_intent_recognitions,
)
from app.core.models import (
    Event,
    ExecutionResultCreate,
    SubTask,
    Task,
    TaskAttachment,
    TaskConfirm,
    TaskContext,
    TaskDraft,
    TaskRound,
    TaskRequestCreate,
    TaskRequestResponse,
    TaskRerunCreate,
    TaskRerunPreflightRequest,
    TaskRerunPreflightResponse,
    TaskRerunResponse,
    User,
    WorkflowDefinition,
    WorkflowTemplate,
    new_id,
    utc_now,
)
from app.services.execution_service import (
    ExecutionService,
    TaskRerunNotAllowedError,
    TaskRerunSideEffectConfirmationRequiredError,
)
from app.services.completion_service import CompletionService
from app.services.artifact_service import ArtifactService
from app.services.deliverable_materializer import DeliverableMaterializer
from app.services.storage import AgentRegistry, InMemoryTaskStore, UserRegistry, WorkflowRegistry
from app.services.task_contract_service import TaskContractService
from app.workflows.task_graph import TaskGraphRunner
from app.workflows.template_runner import WorkflowTemplateRunner


class TaskNotFoundError(Exception):
    pass


class SubTaskNotFoundError(Exception):
    pass


class WorkflowNotFoundError(Exception):
    pass


class AttachmentNotFoundError(Exception):
    pass


class TaskCannotBeCancelledError(Exception):
    pass


class TaskAlreadyConfirmedError(Exception):
    pass


class TaskNotRunningError(Exception):
    pass


class TaskNotConfirmedError(Exception):
    pass


class TaskResultNotAllowedError(Exception):
    pass


class HumanAcceptanceNotPendingError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class TaskRerunIdempotencyConflictError(Exception):
    pass


class TaskRerunIdempotencyKeyRequiredError(Exception):
    pass


class TaskService:
    def __init__(
        self,
        store: InMemoryTaskStore,
        agent_registry: AgentRegistry,
        workflow_registry: WorkflowRegistry | None = None,
        user_registry: UserRegistry | None = None,
        attachment_store=None,
    ) -> None:
        self.store = store
        self.agent_registry = agent_registry
        self.workflow_registry = workflow_registry
        self.user_registry = user_registry
        self.attachment_store = attachment_store
        self.task_contract_service = TaskContractService()
        self.execution_service = ExecutionService()
        self.artifact_service = ArtifactService()
        self.deliverable_materializer = DeliverableMaterializer()
        self.completion_service = CompletionService(
            self.execution_service,
            self.artifact_service,
            self.deliverable_materializer,
        )
        self.task_graph = TaskGraphRunner(
            agent_registry,
            user_registry,
            completion_service=self.completion_service,
            artifact_service=self.artifact_service,
        )
        self.workflow_runner = WorkflowTemplateRunner(
            agent_registry,
            completion_service=self.completion_service,
            artifact_service=self.artifact_service,
        )
        self._task_locks_guard = RLock()
        self._task_locks = {}

    def create_request(self, payload: TaskRequestCreate, created_by: User | None = None) -> TaskRequestResponse:
        request_id = new_id("req")
        request_metadata = self._request_metadata_with_workflow_snapshot(payload.metadata)
        attachments = self._resolve_attachments(payload, request_metadata)
        request_metadata = self._request_metadata_with_attachments(request_metadata, attachments)
        attachment_context = self._format_attachment_context(attachments)
        recognition_content = self._content_with_attachment_context(payload.content, attachment_context)
        task_type = self._task_type_for_request(payload, request_metadata)
        if task_type == TaskType.MANUAL_ORCHESTRATION:
            draft = self._workflow_template_draft(payload, request_metadata)
        else:
            agents = self.agent_registry.list_processing_agents()
            raw_drafts = recognize_tasks_with_model(recognition_content, agents)
            if not raw_drafts:
                require_system_mock_fallback_enabled("intent_recognition")
                raw_drafts = mock_intent_recognitions(recognition_content, agents)
            draft = self._merge_drafts(payload.content, raw_drafts)
        task = Task(
            id=new_id("task"),
            request_id=request_id,
            source_type=payload.source_type,
            description=payload.content,
            content=payload.content,
            task_type=task_type,
            request_metadata=request_metadata,
            created_by_user_id=created_by.id if created_by else "root",
            created_by_user_name=created_by.name if created_by else "管理员",
            task_status=TaskStatus.RUNNING,
            current_node=CurrentNode.HUMAN_CONFIRMATION,
            draft=draft,
            title=payload.title.strip() or draft.title,
            assigned_agent_id=draft.suggested_agent_id,
            context=TaskContext(
                summary=attachment_context,
                artifacts=[self._attachment_artifact_text(attachment) for attachment in attachments],
            ),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        task.events.append(self._event("task_created", f"Main task created from request {request_id}"))
        if attachments:
            task.events.append(self._event("attachments_bound", f"{len(attachments)} text attachments bound to task context"))
        if task_type == TaskType.MANUAL_ORCHESTRATION:
            task.events.append(self._event("workflow_template_selected", "Workflow template created a main task draft"))
        else:
            task.events.append(self._event("intent_recognized", "Intent recognition created a main task draft"))
        return TaskRequestResponse(request_id=request_id, tasks=[self._save(task)])

    def confirm_task(self, task_id: str, payload: TaskConfirm, confirmed_by: User) -> Task:
        task = self.confirm_task_details(task_id, payload, confirmed_by)
        return self.run_confirmed_task(task.id)

    def confirm_task_details(
        self,
        task_id: str,
        payload: TaskConfirm,
        confirmed_by: User,
    ) -> Task:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            if task.current_node != CurrentNode.HUMAN_CONFIRMATION or task.contract is not None:
                raise TaskAlreadyConfirmedError(task_id)
            task.title = payload.title.strip() or task.title
            task.description = payload.description.strip() or task.description
            task.contract = self.task_contract_service.confirm_contract(task, payload, confirmed_by)
            task.request_metadata = self._metadata_with_default_human_assignee(task.request_metadata, payload)
            if self._is_workflow_template_task(task):
                task.context.summary = self._workflow_initial_context_summary(task)
            task.initial_context = task.context.model_copy(deep=True)
            dependencies_satisfied = self._dependencies_satisfied(task)
            task.current_node = (
                CurrentNode.DISPATCH_DECISION if dependencies_satisfied else CurrentNode.WAITING_DEPENDENCIES
            )
            self.execution_service.create_initial(
                task,
                confirmed_by,
                start_node=task.current_node,
                execution_mode=payload.execution_mode,
            )
            task.events.append(self._event("human_confirmed", "Human confirmed task details"))
            if not dependencies_satisfied:
                task.events.append(self._event("dependency_waiting", "Task is waiting for prerequisite tasks"))
                return self._save(task)
            return self._save(task)

    def schedule_confirmed_task(
        self,
        task_id: str,
        expected_execution_id: str | None = None,
    ) -> Task:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            if (
                self._runnable_execution(task, expected_execution_id) is None
                or not self._dependencies_satisfied(task)
            ):
                return task
            task.events.append(
                self._event(
                    "async_execution_scheduled",
                    "Automatic task flow scheduled",
                )
            )
            return self._save(task)

    def start_background_task(
        self,
        task_id: str,
        expected_execution_id: str | None = None,
    ) -> None:
        claimed = self._claim_execution(task_id, expected_execution_id)
        if claimed is None:
            return
        _, execution_id = claimed
        Thread(
            target=self._run_claimed_execution,
            args=(task_id, execution_id),
            daemon=True,
        ).start()

    def run_confirmed_task(
        self,
        task_id: str,
        expected_execution_id: str | None = None,
    ) -> Task:
        claimed = self._claim_execution(task_id, expected_execution_id)
        if claimed is None:
            return self._get_existing(task_id)
        _, execution_id = claimed
        return self._run_claimed_execution(task_id, execution_id)

    def _run_claimed_execution(self, task_id: str, execution_id: str) -> Task:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            execution = self._runnable_execution(task, execution_id)
            if execution is None or execution.started_at is None:
                return task
            working_task = task.model_copy(deep=True)
            try:
                result = self._run_automatic_flow(working_task)
            except Exception as exc:
                current = self._get_existing(task_id)
                if self._runnable_execution(current, execution_id) is None:
                    return current
                error_message = str(exc).strip() or exc.__class__.__name__
                self.completion_service.finalize(
                    current,
                    candidate_status=TaskStatus.FAILED,
                    output=error_message,
                    reason=error_message,
                    decided_by_type="system",
                    decided_by_id="task_service",
                )
                current.events.append(self._event("task_failed", error_message))
                saved = self._save(current)
            else:
                current = self._get_existing(task_id)
                if self._runnable_execution(current, execution_id) is None:
                    return current
                saved = self._save(result)
        self._resume_unblocked_tasks()
        return saved

    def _claim_execution(
        self,
        task_id: str,
        expected_execution_id: str | None,
    ) -> tuple[Task, str] | None:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            execution = self._runnable_execution(task, expected_execution_id)
            if (
                execution is None
                or not self._dependencies_satisfied(task)
            ):
                return None
            if execution.started_at is None:
                execution.started_at = utc_now()
            elif self._started_execution_can_resume_after_human_round(task):
                task.current_node = CurrentNode.DISPATCH_DECISION
            else:
                return None
            saved = self._save(task)
            return saved, execution.id

    @staticmethod
    def _started_execution_can_resume_after_human_round(task: Task) -> bool:
        if task.current_node != CurrentNode.CONTEXT_UPDATE or not task.context.rounds:
            return False
        latest_round = max(task.context.rounds, key=lambda item: item.round_index)
        return any(subtask.assignee_type == "human" for subtask in latest_round.subtasks) and all(
            subtask.status != TaskStatus.RUNNING for subtask in latest_round.subtasks
        )

    def _runnable_execution(
        self,
        task: Task,
        expected_execution_id: str | None,
    ):
        execution = self.execution_service.active(task)
        if task.task_status != TaskStatus.RUNNING or execution is None:
            return None
        if (
            expected_execution_id is not None
            and execution.id != expected_execution_id
        ):
            return None
        if execution.status != TaskStatus.RUNNING or execution.finished_at is not None:
            return None
        return execution

    def submit_result(
        self,
        task_id: str,
        payload: ExecutionResultCreate,
        current_user: User | None = None,
    ) -> Task:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            if task.task_status != TaskStatus.RUNNING:
                raise TaskNotRunningError(task_id)
            if task.current_node == CurrentNode.HUMAN_CONFIRMATION or task.contract is None:
                raise TaskNotConfirmedError(task_id)
            if task.current_node != CurrentNode.HUMAN_INTERVENTION:
                raise TaskResultNotAllowedError(task_id)
            candidate_status = self._task_status_from_result(payload.result_status)
            human_accepted = payload.metadata.get("human_accepted") is True
            pending_review = self._pending_human_review_report(task)
            pending_adjudication = bool(
                pending_review is not None and pending_review.awaiting_human_decision
            )
            if pending_adjudication:
                if candidate_status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED} or not payload.should_complete:
                    raise HumanAcceptanceNotPendingError(task_id)
                human_accepted = candidate_status == TaskStatus.SUCCEEDED
            else:
                if human_accepted and (
                    candidate_status != TaskStatus.SUCCEEDED
                    or not payload.should_complete
                    or pending_review is None
                ):
                    raise HumanAcceptanceNotPendingError(task_id)
                if (
                    not human_accepted
                    and candidate_status == TaskStatus.SUCCEEDED
                    and pending_review is not None
                ):
                    raise HumanAcceptanceNotPendingError(task_id)
            task.events.append(
                self._event(
                    "execution_result_submitted",
                    payload.output or payload.result_status.value,
                )
            )
            if candidate_status != TaskStatus.SUCCEEDED or payload.should_complete:
                result_output = (
                    task.final_output.strip()
                    if pending_review is not None
                    else payload.output.strip() or task.final_output.strip() or task.context.summary.strip()
                )
                if candidate_status != TaskStatus.SUCCEEDED and not result_output:
                    result_output = payload.result_status.value
                reason = (
                    (payload.completion_reason or "").strip()
                    or str(payload.metadata.get("completion_reason") or "").strip()
                    or (payload.output.strip() if pending_review is not None else "")
                    or f"Execution result reported {payload.result_status.value}"
                )
                report = self.completion_service.finalize(
                    task,
                    candidate_status=candidate_status,
                    output=result_output,
                    reason=reason,
                    criterion_results=(
                        pending_review.criterion_results
                        if pending_review is not None
                        else payload.criterion_results
                    ),
                    artifact_ids=(
                        pending_review.artifact_ids
                        if pending_review is not None
                        else payload.artifact_ids
                    ),
                    workflow_end_reached=bool(
                        pending_review is not None
                        and pending_review.workflow_end_node_id
                    ),
                    workflow_end_node_id=(
                        pending_review.workflow_end_node_id
                        if pending_review is not None
                        else None
                    ),
                    human_accepted=human_accepted,
                    human_override=pending_adjudication,
                    decided_by_type="human" if current_user else "external_executor",
                    decided_by_id=current_user.id if current_user else "",
                )
                task.events.append(
                    self._event(
                        "completion_judged",
                        f"Execution result finalized task as {report.terminal_status.value}",
                    )
                )
                saved = self._save(task)
            else:
                saved = self._save(self._run_automatic_flow(task))
        self._resume_unblocked_tasks()
        return saved

    def _pending_human_acceptance_report(self, task: Task):
        report = self._pending_human_review_report(task)
        if (
            report is None
            or report.awaiting_human_decision
            or task.contract is None
            or not task.contract.requires_human_acceptance
        ):
            return None
        return report

    def _pending_human_review_report(self, task: Task):
        report = task.completion_report
        active_execution = self.execution_service.active(task)
        if (
            task.task_status != TaskStatus.RUNNING
            or task.current_node != CurrentNode.HUMAN_INTERVENTION
            or active_execution is None
            or active_execution.status != TaskStatus.RUNNING
            or active_execution.finished_at is not None
            or report is None
            or report.execution_id != active_execution.id
            or report.terminal_status != TaskStatus.RUNNING
            or report.human_accepted
            or report.decided_by_type != "system"
            or active_execution.completion_report != report
        ):
            return None
        if not report.awaiting_human_decision and (
            task.contract is None or not task.contract.requires_human_acceptance
        ):
            return None
        return report

    def get_task(self, task_id: str) -> Task:
        return self._get_existing(task_id)

    def list_tasks(self) -> list[Task]:
        return self.store.list()

    def list_executions(self, task_id: str):
        return self.execution_service.list(self._get_existing(task_id))

    def get_execution(self, task_id: str, execution_id: str):
        return self.execution_service.get(self._get_existing(task_id), execution_id)

    def preflight_rerun(
        self,
        task_id: str,
        payload: TaskRerunPreflightRequest,
    ) -> TaskRerunPreflightResponse:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            return self.execution_service.preflight(
                task,
                payload,
                dependencies_satisfied=self._dependencies_satisfied(task),
            )

    def create_rerun(
        self,
        task_id: str,
        payload: TaskRerunCreate,
        actor: User,
        idempotency_key: str,
    ) -> TaskRerunResponse:
        normalized_key = idempotency_key.strip()
        if not normalized_key:
            raise TaskRerunIdempotencyKeyRequiredError(task_id)
        fingerprint = self._rerun_fingerprint(payload)
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            existing = next(
                (
                    execution
                    for execution in task.executions
                    if execution.idempotency_key == normalized_key
                ),
                None,
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise TaskRerunIdempotencyConflictError(normalized_key)
                return TaskRerunResponse(
                    task=task,
                    execution=existing,
                    replayed=True,
                    scheduled=False,
                    execution_is_active=task.active_execution_id == existing.id,
                )

            dependencies_satisfied = self._dependencies_satisfied(task)
            preflight = self.execution_service.preflight(
                task,
                TaskRerunPreflightRequest(
                    source_execution_id=payload.source_execution_id
                ),
                dependencies_satisfied=dependencies_satisfied,
            )
            execution = self.execution_service.create_rerun(
                task,
                payload,
                actor,
                idempotency_key=normalized_key,
                request_fingerprint=fingerprint,
                start_node=preflight.start_node,
                dependencies_satisfied=dependencies_satisfied,
                preflight=preflight,
            )
            task.events.append(
                self._event(
                    "task_rerun_created",
                    f"Execution {execution.id} created from {payload.source_execution_id}",
                )
            )
            saved = self._save(task)
            return TaskRerunResponse(
                task=saved,
                execution=self.execution_service.get(saved, execution.id),
                replayed=False,
                scheduled=False,
                execution_is_active=saved.active_execution_id == execution.id,
            )

    def cancel_unconfirmed_task(self, task_id: str, cancelled_by: User) -> Task:
        with self._task_lock(task_id):
            task = self._get_existing(task_id)
            if task.current_node != CurrentNode.HUMAN_CONFIRMATION or task.task_status != TaskStatus.RUNNING:
                raise TaskCannotBeCancelledError(task_id)
            reason = "Cancelled before confirmation"
            self.completion_service.finalize(
                task,
                candidate_status=TaskStatus.CANCELLED,
                output=reason,
                reason=reason,
                decided_by_type="human",
                decided_by_id=cancelled_by.id,
            )
            task.events.append(self._event("task_cancelled", f"{cancelled_by.name}: {reason}"))
            return self._save(task)

    def list_human_subtasks(
        self,
        assignee_user_id: str | None = None,
        current_user: User | None = None,
    ) -> list[SubTask]:
        effective_assignee_user_id = assignee_user_id
        if current_user and current_user.role != UserRole.ADMIN:
            effective_assignee_user_id = current_user.id
        subtasks = []
        for task in self.store.list():
            for round_item in task.context.rounds:
                for subtask in round_item.subtasks:
                    if subtask.assignee_type == "human" and subtask.status == TaskStatus.RUNNING:
                        if effective_assignee_user_id and subtask.assignee_user_id != effective_assignee_user_id:
                            continue
                        subtasks.append(self._human_subtask_view(task, round_item, subtask))
        return subtasks

    def submit_subtask_result(
        self,
        subtask_id: str,
        payload: ExecutionResultCreate,
        resume_flow: bool = True,
        current_user: User | None = None,
    ) -> Task:
        located_task, _, _ = self._find_subtask(subtask_id)
        resume_dependencies = False
        with self._task_lock(located_task.id):
            task, round_index, subtask = self._find_subtask(subtask_id)
            if task.task_status != TaskStatus.RUNNING:
                raise TaskNotRunningError(task.id)
            if current_user and current_user.role != UserRole.ADMIN and subtask.assignee_user_id != current_user.id:
                raise PermissionDeniedError(subtask_id)
            subtask.output = payload.output or payload.result_status.value
            subtask.result_metadata = payload.metadata
            subtask.status = self._task_status_from_result(payload.result_status)
            subtask.current_node = CurrentNode.HUMAN_EXECUTION
            if subtask.status == TaskStatus.SUCCEEDED:
                self.artifact_service.register_subtask_output(task, subtask, subtask.output)
            task.events.append(self._event("human_result_submitted", f"{subtask.title}: {subtask.output}"))
            if subtask.status != TaskStatus.SUCCEEDED:
                self._merge_completed_round(task, round_index)
                reason = (
                    (payload.completion_reason or "").strip()
                    or str(payload.metadata.get("completion_reason") or "").strip()
                    or f"Human subtask reported {payload.result_status.value}"
                )
                report = self.completion_service.finalize(
                    task,
                    candidate_status=subtask.status,
                    output=subtask.output,
                    reason=reason,
                    criterion_results=payload.criterion_results,
                    artifact_ids=payload.artifact_ids,
                    human_accepted=bool(payload.metadata.get("human_accepted")),
                    decided_by_type="human" if current_user else "external_executor",
                    decided_by_id=current_user.id if current_user else "",
                )
                task.events.append(
                    self._event("completion_judged", f"Human result finalized task as {report.terminal_status.value}")
                )
                saved = self._save(task)
                resume_dependencies = True
            elif self._round_has_running_subtasks(task, round_index):
                task.current_node = CurrentNode.HUMAN_EXECUTION
                saved = self._save(task)
            else:
                self._merge_completed_round(task, round_index)
                if not resume_flow:
                    task.events.append(
                        self._event(
                            "async_execution_scheduled",
                            "Automatic task flow scheduled after human result",
                        )
                    )
                    saved = self._save(task)
                else:
                    saved = self._save(self._run_automatic_flow(task))
                    resume_dependencies = True
        if resume_dependencies:
            self._resume_unblocked_tasks()
        return saved

    def _run_automatic_flow(self, task: Task) -> Task:
        self.execution_service.mark_started(task)
        if self._is_workflow_template_task(task):
            return self.workflow_runner.run(task, self._get_task_workflow(task))
        return self.task_graph.run(task)

    @staticmethod
    def _human_subtask_view(task: Task, current_round: TaskRound, subtask: SubTask) -> SubTask:
        task_title = task.title or (task.draft.title if task.draft else "") or task.id
        review_description = TaskService._workflow_human_handoff_instruction(task, subtask) or subtask.description
        return subtask.model_copy(
            update={
                "description": review_description,
                "task_id": task.id,
                "task_title": task_title,
                "task_description": task.description,
                "task_content": task.content,
                "task_context_summary": task.context.summary,
                "task_artifacts": task.context.artifacts,
                "upstream_outputs": TaskService._human_subtask_upstream_outputs(task, current_round, subtask),
            }
        )

    @staticmethod
    def _workflow_human_handoff_instruction(task: Task, subtask: SubTask) -> str:
        workflow_definition = task.request_metadata.get("workflow_definition")
        if not workflow_definition:
            return ""
        node_id = subtask.logical_key or TaskService._workflow_node_id_from_subtask_id(
            task.id,
            subtask.id,
            subtask.execution_id,
        )
        if not node_id:
            return ""
        try:
            definition = WorkflowDefinition.model_validate(workflow_definition)
        except (TypeError, ValueError):
            return ""
        for node in definition.nodes:
            if node.type == "human" and node.id == node_id:
                return str(node.config.get("handoff_instruction") or "").strip()
        return ""

    @staticmethod
    def _workflow_node_id_from_subtask_id(task_id: str, subtask_id: str, execution_id: str = "") -> str:
        if execution_id:
            execution_prefix = f"{task_id}_{execution_id}_"
            if subtask_id.startswith(execution_prefix):
                return subtask_id[len(execution_prefix) :]
        prefix = f"{task_id}_"
        if not subtask_id.startswith(prefix):
            return ""
        return subtask_id[len(prefix) :]

    @staticmethod
    def _human_subtask_upstream_outputs(task: Task, current_round: TaskRound, active_subtask: SubTask) -> list[str]:
        outputs: list[str] = []
        for round_item in task.context.rounds:
            for candidate in round_item.subtasks:
                if candidate.id == active_subtask.id:
                    continue
                if candidate.status == TaskStatus.SUCCEEDED and candidate.output:
                    outputs.append(f"{candidate.title}: {candidate.output}")
            if round_item.round_index == current_round.round_index:
                break
        return outputs

    def _is_workflow_template_task(self, task: Task) -> bool:
        return task.task_type == TaskType.MANUAL_ORCHESTRATION or task.request_metadata.get("execution_mode") == "workflow_template"

    @staticmethod
    def _task_type_for_request(payload: TaskRequestCreate, request_metadata: dict) -> TaskType:
        if request_metadata.get("execution_mode") == "workflow_template":
            return TaskType.MANUAL_ORCHESTRATION
        if payload.task_type:
            return payload.task_type
        return TaskType.AUTO_PLANNING

    def _get_task_workflow(self, task: Task):
        active_execution = self.execution_service.active(task)
        workflow_definition = (
            active_execution.workflow_snapshot
            if active_execution is not None
            and active_execution.workflow_snapshot is not None
            else task.request_metadata.get("workflow_definition")
        )
        if workflow_definition is not None:
            return WorkflowTemplate(
                id=str(task.request_metadata.get("workflow_id") or f"{task.id}_workflow"),
                name=str(task.request_metadata.get("workflow_name") or task.title or "Task workflow"),
                description=str(task.request_metadata.get("workflow_description") or ""),
                definition=WorkflowDefinition.model_validate(workflow_definition),
                status="active",
                created_at=task.created_at,
                updated_at=task.created_at,
            )
        workflow_id = task.request_metadata.get("workflow_id")
        if not workflow_id or self.workflow_registry is None:
            raise WorkflowNotFoundError(workflow_id or "")
        workflow = self.workflow_registry.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(workflow_id)
        return workflow

    def _request_metadata_with_workflow_snapshot(self, metadata: dict) -> dict:
        request_metadata = dict(metadata)
        if request_metadata.get("execution_mode") != "workflow_template":
            return request_metadata
        if request_metadata.get("workflow_definition"):
            definition = WorkflowDefinition.model_validate(request_metadata["workflow_definition"])
            request_metadata["workflow_definition"] = definition.model_dump(mode="json", by_alias=True)
            return request_metadata
        workflow_id = request_metadata.get("workflow_id")
        if not workflow_id or self.workflow_registry is None:
            raise WorkflowNotFoundError(str(workflow_id or ""))
        workflow = self.workflow_registry.get_workflow(str(workflow_id))
        if workflow is None:
            raise WorkflowNotFoundError(str(workflow_id))
        request_metadata["workflow_name"] = workflow.name
        request_metadata["workflow_description"] = workflow.description
        request_metadata["workflow_definition"] = workflow.definition.model_dump(mode="json", by_alias=True)
        return request_metadata

    def _resolve_attachments(self, payload: TaskRequestCreate, request_metadata: dict) -> list[TaskAttachment]:
        raw_attachment_ids = payload.attachment_ids or request_metadata.get("attachment_ids") or []
        if not raw_attachment_ids:
            return []
        if self.attachment_store is None:
            raise AttachmentNotFoundError("attachment store is not configured")
        attachments = []
        for attachment_id in raw_attachment_ids:
            attachment = self.attachment_store.get(str(attachment_id))
            if attachment is None:
                raise AttachmentNotFoundError(str(attachment_id))
            attachments.append(attachment)
        return attachments

    @staticmethod
    def _request_metadata_with_attachments(request_metadata: dict, attachments: list[TaskAttachment]) -> dict:
        if not attachments:
            return request_metadata
        next_metadata = dict(request_metadata)
        next_metadata["attachment_ids"] = [attachment.id for attachment in attachments]
        next_metadata["attachments"] = [
            {
                "id": attachment.id,
                "filename": attachment.filename,
                "extension": attachment.extension,
                "size_bytes": attachment.size_bytes,
                "text_length": attachment.text_length,
                "truncated": attachment.truncated,
                "status": attachment.status,
                "text_preview": attachment.text_preview,
            }
            for attachment in attachments
        ]
        return next_metadata

    @staticmethod
    def _format_attachment_context(attachments: list[TaskAttachment]) -> str:
        if not attachments:
            return ""
        parts = []
        for attachment in attachments:
            if attachment.status != "parsed":
                parts.append(f"附件 {attachment.filename} 解析失败：{attachment.error or 'unknown error'}")
                continue
            parts.append(
                f"附件：{attachment.filename}\n"
                f"类型：{attachment.extension}，字符数：{attachment.text_length}"
                f"{'，内容已截断' if attachment.truncated else ''}\n"
                f"{attachment.text_content}"
            )
        return "\n\n".join(parts).strip()

    @staticmethod
    def _content_with_attachment_context(content: str, attachment_context: str) -> str:
        if not attachment_context:
            return content
        return f"{content}\n\n以下是用户上传附件解析出的纯文本内容：\n{attachment_context}"

    @staticmethod
    def _attachment_artifact_text(attachment: TaskAttachment) -> str:
        return f"{attachment.filename}（{attachment.extension}，{attachment.text_length} 字符）"

    @staticmethod
    def _workflow_template_draft(payload: TaskRequestCreate, request_metadata: dict) -> TaskDraft:
        title = payload.title.strip() or str(request_metadata.get("workflow_name") or "流程模板任务")
        return TaskDraft(
            title=title,
            description=payload.content,
            confidence=1.0,
            suggested_assignee_type="human",
            suggested_agent_id=None,
        )

    @staticmethod
    def _workflow_initial_context_summary(task: Task) -> str:
        parts = []
        title = task.title.strip()
        description = task.description.strip()
        existing_summary = task.context.summary.strip()
        if title:
            parts.append(f"任务名称：{title}")
        if description:
            parts.append(f"任务诉求：{description}")
        if existing_summary and existing_summary not in {title, description}:
            parts.append(f"补充上下文：{existing_summary}")
        return "\n".join(parts).strip()

    def _metadata_with_default_human_assignee(self, request_metadata: dict, payload: TaskConfirm) -> dict:
        assignee_user_id = payload.default_assignee_user_id.strip()
        assignee_user_name = payload.default_assignee_user_name.strip()
        assignee_role = payload.default_assignee_role.strip()
        if not assignee_user_id and not assignee_user_name:
            return request_metadata

        if self.user_registry is not None and assignee_user_id:
            user = self.user_registry.get_user(assignee_user_id)
            if user and user.status == "active":
                assignee_user_name = user.name
                assignee_role = user.role.value
            else:
                root = self.user_registry.get_user("root")
                assignee_user_id = root.id if root else "root"
                assignee_user_name = root.name if root else "管理员"
                assignee_role = root.role.value if root else "admin"

        if not assignee_user_id:
            assignee_user_id = assignee_user_name
        if not assignee_user_name:
            assignee_user_name = "管理员" if assignee_user_id == "root" else assignee_user_id
        if not assignee_role:
            assignee_role = "admin" if assignee_user_id == "root" else "approver"

        next_metadata = dict(request_metadata)
        next_metadata["default_human_assignee"] = {
            "assignee_user_id": assignee_user_id,
            "assignee_user_name": assignee_user_name,
            "assignee_role": assignee_role,
        }
        return next_metadata

    def _find_subtask(self, subtask_id: str) -> tuple[Task, int, SubTask]:
        legacy_matches: list[tuple[Task, int, SubTask]] = []
        for task in self.store.list():
            for round_item in task.context.rounds:
                for subtask in round_item.subtasks:
                    if subtask.id == subtask_id:
                        if (
                            subtask.execution_id
                            and subtask.execution_id != task.active_execution_id
                        ):
                            continue
                        return task, round_item.round_index, subtask
                    if subtask.execution_id or not subtask.logical_key:
                        continue
                    legacy_aliases = {
                        subtask.logical_key,
                        f"{task.id}_{subtask.logical_key}",
                    }
                    if subtask_id in legacy_aliases:
                        legacy_matches.append(
                            (task, round_item.round_index, subtask)
                        )
        if len(legacy_matches) == 1:
            return legacy_matches[0]
        raise SubTaskNotFoundError(subtask_id)

    @staticmethod
    def _round_has_running_subtasks(task: Task, round_index: int) -> bool:
        round_item = next(item for item in task.context.rounds if item.round_index == round_index)
        return any(subtask.status == TaskStatus.RUNNING for subtask in round_item.subtasks)

    def _merge_completed_round(self, task: Task, round_index: int) -> None:
        round_item = next(item for item in task.context.rounds if item.round_index == round_index)
        output_text = "\n".join(
            self._format_subtask_context(subtask) for subtask in round_item.subtasks if subtask.output
        ).strip()
        round_item.context_after = self._build_context_summary(round_item.context_before, output_text)
        task.context.summary = round_item.context_after
        task.current_node = CurrentNode.CONTEXT_UPDATE
        task.events.append(self._event("context_updated", f"Round {round_index} results merged into context"))
        task.updated_at = utc_now()

    def _dependencies_satisfied(self, task: Task) -> bool:
        for dependency_task_id in task.dependency_task_ids:
            dependency = self.store.get(dependency_task_id)
            if dependency is None or dependency.task_status != TaskStatus.SUCCEEDED:
                return False
        return True

    def _resume_unblocked_tasks(self) -> None:
        ready_tasks: list[tuple[str, str | None]] = []
        for listed_task in self.store.list():
            with self._task_lock(listed_task.id):
                candidate = self._get_existing(listed_task.id)
                if candidate.current_node != CurrentNode.WAITING_DEPENDENCIES:
                    continue
                if candidate.task_status != TaskStatus.RUNNING:
                    continue
                if not candidate.title or not candidate.description:
                    continue
                if not self._dependencies_satisfied(candidate):
                    continue
                candidate.context.summary = self._build_dependency_context(candidate)
                candidate.events.append(self._event("dependency_released", "Prerequisite tasks completed"))
                saved = self._save(candidate)
                ready_tasks.append((saved.id, saved.active_execution_id))
        for task_id, execution_id in ready_tasks:
            self.run_confirmed_task(
                task_id,
                expected_execution_id=execution_id,
            )

    def _get_existing(self, task_id: str) -> Task:
        task = self.store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _task_lock(self, task_id: str):
        with self._task_locks_guard:
            lock = self._task_locks.get(task_id)
            if lock is None:
                lock = RLock()
                self._task_locks[task_id] = lock
            return lock

    def _save(self, task: Task) -> Task:
        self.execution_service.sync_projection(task)
        return self.store.save(task)

    @staticmethod
    def _rerun_fingerprint(payload: TaskRerunCreate) -> str:
        canonical = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _task_status_from_result(result_status: ResultStatus) -> TaskStatus:
        return {
            ResultStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
            ResultStatus.FAILED: TaskStatus.FAILED,
            ResultStatus.BLOCKED: TaskStatus.BLOCKED,
            ResultStatus.PARTIAL: TaskStatus.PARTIAL,
        }[result_status]

    @staticmethod
    def _event(event_type: str, message: str) -> Event:
        return Event(type=event_type, message=message, created_at=utc_now())

    @staticmethod
    def _coerce_draft(raw_draft: TaskDraft | dict) -> TaskDraft:
        if isinstance(raw_draft, TaskDraft):
            return raw_draft
        return TaskDraft.model_validate(raw_draft)

    def _merge_drafts(self, content: str, raw_drafts: list[TaskDraft | dict]) -> TaskDraft:
        drafts = [self._coerce_draft(raw_draft) for raw_draft in raw_drafts]
        if not drafts:
            return TaskDraft(title=content, description=content, confidence=0.5)
        if len(drafts) == 1:
            return drafts[0]
        file_draft = next((draft for draft in drafts if draft.deliverable_kind == "file"), None)
        return TaskDraft(
            title="; ".join(draft.title for draft in drafts),
            description="\n".join(f"- {draft.title}: {draft.description}" for draft in drafts),
            confidence=min(draft.confidence for draft in drafts),
            suggested_assignee_type=drafts[0].suggested_assignee_type,
            suggested_agent_id=drafts[0].suggested_agent_id,
            goal=content,
            deliverable_goal="; ".join(draft.deliverable_goal or draft.title for draft in drafts),
            deliverable_kind=file_draft.deliverable_kind if file_draft else "text",
            deliverable_format=file_draft.deliverable_format if file_draft else None,
            deliverable_filename=file_draft.deliverable_filename if file_draft else "",
            deliverable_requirements=[
                requirement
                for draft in drafts
                for requirement in draft.deliverable_requirements
            ],
            success_criteria=[criterion for draft in drafts for criterion in draft.success_criteria],
            requires_human_acceptance=any(draft.requires_human_acceptance for draft in drafts),
        )

    def _build_dependency_context(self, task: Task) -> str:
        context_parts = []
        for dependency_task_id in task.dependency_task_ids:
            dependency = self.store.get(dependency_task_id)
            if dependency is None:
                continue
            result = dependency.final_output or dependency.context.summary
            if result:
                context_parts.append(f"前置任务 {dependency.title or dependency.id} 结果:\n{result}")
        return "\n\n".join(context_parts)

    @staticmethod
    def _build_context_summary(previous_summary: str, output_text: str) -> str:
        if previous_summary and output_text:
            return f"{previous_summary}\n{output_text}"
        return output_text or previous_summary

    @staticmethod
    def _format_subtask_context(subtask: SubTask) -> str:
        if subtask.status == TaskStatus.FAILED:
            return f"FAILED: {subtask.title}\nReason: {subtask.output}"
        return subtask.output
