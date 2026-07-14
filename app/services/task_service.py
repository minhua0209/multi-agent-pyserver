from threading import Thread

from app.core.enums import CurrentNode, ResultStatus, TaskStatus
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
    TaskConfirm,
    TaskDraft,
    TaskRequestCreate,
    TaskRequestResponse,
    new_id,
    utc_now,
)
from app.services.storage import AgentRegistry, InMemoryTaskStore, WorkflowRegistry
from app.workflows.task_graph import TaskGraphRunner
from app.workflows.template_runner import WorkflowTemplateRunner


class TaskNotFoundError(Exception):
    pass


class SubTaskNotFoundError(Exception):
    pass


class WorkflowNotFoundError(Exception):
    pass


class TaskService:
    def __init__(
        self,
        store: InMemoryTaskStore,
        agent_registry: AgentRegistry,
        workflow_registry: WorkflowRegistry | None = None,
    ) -> None:
        self.store = store
        self.agent_registry = agent_registry
        self.workflow_registry = workflow_registry
        self.task_graph = TaskGraphRunner(agent_registry)
        self.workflow_runner = WorkflowTemplateRunner(agent_registry)

    def create_request(self, payload: TaskRequestCreate) -> TaskRequestResponse:
        request_id = new_id("req")
        agents = self.agent_registry.list_agents()
        raw_drafts = recognize_tasks_with_model(payload.content, agents)
        if not raw_drafts:
            require_system_mock_fallback_enabled("intent_recognition")
            raw_drafts = mock_intent_recognitions(payload.content, agents)
        draft = self._merge_drafts(payload.content, raw_drafts)
        task = Task(
            id=new_id("task"),
            request_id=request_id,
            source_type=payload.source_type,
            content=payload.content,
            request_metadata=payload.metadata,
            task_status=TaskStatus.RUNNING,
            current_node=CurrentNode.HUMAN_CONFIRMATION,
            draft=draft,
            assigned_agent_id=draft.suggested_agent_id,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        task.events.append(self._event("task_created", f"Main task created from request {request_id}"))
        task.events.append(self._event("intent_recognized", "Intent recognition created a main task draft"))
        return TaskRequestResponse(request_id=request_id, tasks=[self.store.save(task)])

    def confirm_task(self, task_id: str, payload: TaskConfirm) -> Task:
        task = self.confirm_task_details(task_id, payload)
        return self.run_confirmed_task(task.id)

    def confirm_task_details(self, task_id: str, payload: TaskConfirm) -> Task:
        task = self._get_existing(task_id)
        task.title = payload.title
        task.description = payload.description
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
        result = self._run_automatic_flow(task)
        self._resume_unblocked_tasks()
        return result

    def submit_result(self, task_id: str, payload: ExecutionResultCreate) -> Task:
        task = self._get_existing(task_id)
        task.events.append(self._event("execution_result_submitted", payload.output or payload.result_status.value))
        task.current_node = CurrentNode.COMPLETION_JUDGE
        if payload.result_status == ResultStatus.FAILED:
            task.task_status = TaskStatus.FAILED
            task.events.append(self._event("completion_judged", "Execution result failed task"))
            saved = self.store.save(task)
            self._resume_unblocked_tasks()
            return saved
        if payload.should_complete:
            task.task_status = TaskStatus.SUCCEEDED
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

    def list_human_subtasks(self) -> list[SubTask]:
        subtasks = []
        for task in self.store.list():
            for round_item in task.context.rounds:
                for subtask in round_item.subtasks:
                    if subtask.assignee_type == "human" and subtask.status == TaskStatus.RUNNING:
                        subtasks.append(subtask)
        return subtasks

    def submit_subtask_result(self, subtask_id: str, payload: ExecutionResultCreate) -> Task:
        task, round_index, subtask = self._find_subtask(subtask_id)
        subtask.output = payload.output or payload.result_status.value
        subtask.status = TaskStatus.FAILED if payload.result_status == ResultStatus.FAILED else TaskStatus.SUCCEEDED
        subtask.current_node = CurrentNode.HUMAN_EXECUTION
        task.events.append(self._event("human_result_submitted", f"{subtask.title}: {subtask.output}"))
        if self._round_has_running_subtasks(task, round_index):
            task.current_node = CurrentNode.HUMAN_EXECUTION
            return self.store.save(task)

        self._merge_completed_round(task, round_index)
        result = self._run_automatic_flow(task)
        self._resume_unblocked_tasks()
        return result

    def _run_automatic_flow(self, task: Task) -> Task:
        if self._is_workflow_template_task(task):
            return self.store.save(self.workflow_runner.run(task, self._get_task_workflow(task)))
        return self.store.save(self.task_graph.run(task))

    def _is_workflow_template_task(self, task: Task) -> bool:
        return task.request_metadata.get("execution_mode") == "workflow_template"

    def _get_task_workflow(self, task: Task):
        workflow_id = task.request_metadata.get("workflow_id")
        if not workflow_id or self.workflow_registry is None:
            raise WorkflowNotFoundError(workflow_id or "")
        workflow = self.workflow_registry.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(workflow_id)
        return workflow

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
