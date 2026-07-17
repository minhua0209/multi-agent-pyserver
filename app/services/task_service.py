from threading import Thread

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
    User,
    WorkflowDefinition,
    WorkflowTemplate,
    new_id,
    utc_now,
)
from app.services.storage import AgentRegistry, InMemoryTaskStore, UserRegistry, WorkflowRegistry
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


class PermissionDeniedError(Exception):
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
        self.task_graph = TaskGraphRunner(agent_registry, user_registry)
        self.workflow_runner = WorkflowTemplateRunner(agent_registry)

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
        return TaskRequestResponse(request_id=request_id, tasks=[self.store.save(task)])

    def confirm_task(self, task_id: str, payload: TaskConfirm) -> Task:
        task = self.confirm_task_details(task_id, payload)
        return self.run_confirmed_task(task.id)

    def confirm_task_details(self, task_id: str, payload: TaskConfirm) -> Task:
        task = self._get_existing(task_id)
        task.title = payload.title.strip() or task.title
        task.description = payload.description.strip() or task.description
        task.request_metadata = self._metadata_with_default_human_assignee(task.request_metadata, payload)
        task.events.append(self._event("human_confirmed", "Human confirmed task details"))
        if not self._dependencies_satisfied(task):
            task.current_node = CurrentNode.WAITING_DEPENDENCIES
            task.events.append(self._event("dependency_waiting", "Task is waiting for prerequisite tasks"))
            return self.store.save(task)
        task.current_node = CurrentNode.DISPATCH_DECISION
        return self.store.save(task)

    def schedule_confirmed_task(self, task_id: str) -> Task:
        task = self._get_existing(task_id)
        task.events.append(self._event("async_execution_scheduled", "Automatic task flow scheduled"))
        return self.store.save(task)

    def start_background_task(self, task_id: str) -> None:
        Thread(target=self.run_confirmed_task, args=(task_id,), daemon=True).start()

    def run_confirmed_task(self, task_id: str) -> Task:
        task = self._get_existing(task_id)
        if not self._dependencies_satisfied(task):
            task.current_node = CurrentNode.WAITING_DEPENDENCIES
            task.events.append(self._event("dependency_waiting", "Task is waiting for prerequisite tasks"))
            return self.store.save(task)
        try:
            result = self._run_automatic_flow(task)
        except Exception as exc:
            task.task_status = TaskStatus.FAILED
            task.current_node = CurrentNode.COMPLETION_JUDGE
            task.final_output = str(exc)
            task.events.append(self._event("task_failed", str(exc)))
            task.updated_at = utc_now()
            return self.store.save(task)
        self._resume_unblocked_tasks()
        return result

    def submit_result(self, task_id: str, payload: ExecutionResultCreate) -> Task:
        task = self._get_existing(task_id)
        task.events.append(self._event("execution_result_submitted", payload.output or payload.result_status.value))
        task.current_node = CurrentNode.COMPLETION_JUDGE
        result_output = payload.output.strip() or task.final_output or task.context.summary or payload.result_status.value
        if payload.result_status == ResultStatus.FAILED:
            task.task_status = TaskStatus.FAILED
            task.final_output = result_output
            task.events.append(self._event("completion_judged", "Execution result failed task"))
            saved = self.store.save(task)
            self._resume_unblocked_tasks()
            return saved
        if payload.should_complete:
            task.task_status = TaskStatus.SUCCEEDED
            task.final_output = result_output
            task.events.append(self._event("completion_judged", "Execution result completed task"))
            saved = self.store.save(task)
            self._resume_unblocked_tasks()
            return saved
        result = self._run_automatic_flow(task)
        self._resume_unblocked_tasks()
        return result

    def get_task(self, task_id: str) -> Task:
        return self._get_existing(task_id)

    def list_tasks(self) -> list[Task]:
        return self.store.list()

    def cancel_unconfirmed_task(self, task_id: str) -> None:
        task = self._get_existing(task_id)
        if task.current_node != CurrentNode.HUMAN_CONFIRMATION:
            raise TaskCannotBeCancelledError(task_id)
        self.store.delete(task_id)

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
        task, round_index, subtask = self._find_subtask(subtask_id)
        if current_user and current_user.role != UserRole.ADMIN and subtask.assignee_user_id != current_user.id:
            raise PermissionDeniedError(subtask_id)
        subtask.output = payload.output or payload.result_status.value
        subtask.result_metadata = payload.metadata
        subtask.status = TaskStatus.FAILED if payload.result_status == ResultStatus.FAILED else TaskStatus.SUCCEEDED
        subtask.current_node = CurrentNode.HUMAN_EXECUTION
        task.events.append(self._event("human_result_submitted", f"{subtask.title}: {subtask.output}"))
        if self._round_has_running_subtasks(task, round_index):
            task.current_node = CurrentNode.HUMAN_EXECUTION
            return self.store.save(task)

        self._merge_completed_round(task, round_index)
        if not resume_flow:
            task.events.append(self._event("async_execution_scheduled", "Automatic task flow scheduled after human result"))
            return self.store.save(task)
        result = self._run_automatic_flow(task)
        self._resume_unblocked_tasks()
        return result

    def _run_automatic_flow(self, task: Task) -> Task:
        if self._is_workflow_template_task(task):
            return self.store.save(self.workflow_runner.run(task, self._get_task_workflow(task)))
        return self.store.save(self.task_graph.run(task))

    @staticmethod
    def _human_subtask_view(task: Task, current_round: TaskRound, subtask: SubTask) -> SubTask:
        task_title = task.title or (task.draft.title if task.draft else "") or task.id
        return subtask.model_copy(
            update={
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
        workflow_definition = task.request_metadata.get("workflow_definition")
        if workflow_definition:
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
        for task in self.store.list():
            for round_item in task.context.rounds:
                for subtask in round_item.subtasks:
                    if subtask.id == subtask_id:
                        return task, round_item.round_index, subtask
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
        for candidate in self.store.list():
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
            self._run_automatic_flow(candidate)

    def _get_existing(self, task_id: str) -> Task:
        task = self.store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

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
        return TaskDraft(
            title="; ".join(draft.title for draft in drafts),
            description="\n".join(f"- {draft.title}: {draft.description}" for draft in drafts),
            confidence=min(draft.confidence for draft in drafts),
            suggested_assignee_type=drafts[0].suggested_assignee_type,
            suggested_agent_id=drafts[0].suggested_agent_id,
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
