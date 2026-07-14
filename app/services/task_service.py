from app.core.enums import CurrentNode, ResultStatus, TaskStatus
from app.core.model_client import recognize_tasks_with_model
from app.core.mock_llm import (
    mock_intent_recognitions,
)
from app.core.models import (
    Event,
    ExecutionResultCreate,
    Task,
    TaskConfirm,
    TaskDraft,
    TaskRequestCreate,
    TaskRequestResponse,
    new_id,
    utc_now,
)
from app.services.storage import AgentRegistry, InMemoryTaskStore
from app.workflows.task_graph import TaskGraphRunner


class TaskNotFoundError(Exception):
    pass


class TaskService:
    def __init__(self, store: InMemoryTaskStore, agent_registry: AgentRegistry) -> None:
        self.store = store
        self.agent_registry = agent_registry
        self.task_graph = TaskGraphRunner(agent_registry)

    def create_request(self, payload: TaskRequestCreate) -> TaskRequestResponse:
        request_id = new_id("req")
        agents = self.agent_registry.list_agents()
        raw_drafts = recognize_tasks_with_model(payload.content, agents) or mock_intent_recognitions(
            payload.content,
            agents,
        )
        draft = self._merge_drafts(payload.content, raw_drafts)
        task = Task(
            id=new_id("task"),
            source_type=payload.source_type,
            content=payload.content,
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
        task = self._get_existing(task_id)
        task.title = payload.title
        task.description = payload.description
        task.events.append(self._event("human_confirmed", "Human confirmed task details"))
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

    def _run_automatic_flow(self, task: Task) -> Task:
        return self.store.save(self.task_graph.run(task))

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
