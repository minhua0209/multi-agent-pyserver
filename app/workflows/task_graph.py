from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import hashlib
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.core.config import require_system_mock_fallback_enabled
from app.core.enums import CurrentNode, TaskStatus
from app.core.mock_llm import mock_agent_execution, mock_dispatch, mock_human_node_processing, mock_round_plan
from app.core.model_client import execute_subtask_with_tools_model
from app.core.models import Event, RoundPlan, SubTask, Task, TaskRound, User, utc_now
from app.planners.factory import get_task_planner
from app.services.completion_service import CompletionService
from app.services.artifact_service import ArtifactService
from app.services.storage import AgentRegistry, UserRegistry
from app.services.tool_executor import ToolExecutor


class TaskGraphState(TypedDict):
    task: Task
    round_plan: RoundPlan
    round_outputs: list[str]
    paused: bool


RouteAfterPlan = Literal["subtask_execution", "completion_judge", "human_intervention"]
RouteAfterSubTask = Literal["context_update", "end"]
RouteAfterJudge = Literal["round_dispatch", "human_intervention", "end"]


@dataclass(frozen=True)
class SubTaskExecutionOutcome:
    completed: bool
    output: str = ""
    error: str = ""

    @property
    def context_text(self) -> str:
        if self.completed:
            return self.output
        reason = self.error or self.output or "Subtask did not complete"
        return reason


def plan_next_round_with_model(task: Task, agents: list) -> RoundPlan | None:
    return get_task_planner().plan_next_round(task, agents)


class TaskGraphRunner:
    max_parallel_agent_subtasks = 4

    def __init__(
        self,
        agent_registry: AgentRegistry,
        user_registry: UserRegistry | None = None,
        completion_service: CompletionService | None = None,
        artifact_service: ArtifactService | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.user_registry = user_registry
        self.artifact_service = (
            artifact_service
            or (completion_service.artifact_service if completion_service else None)
            or ArtifactService()
        )
        self.completion_service = completion_service or CompletionService(
            artifact_service=self.artifact_service
        )
        self.tool_executor = ToolExecutor()
        self.graph = self._build_graph()

    def run(self, task: Task) -> Task:
        final_state = self.graph.invoke(
            {
                "task": task,
                "round_plan": RoundPlan(should_continue=False),
                "round_outputs": [],
                "paused": False,
            }
        )
        return final_state["task"]

    def _build_graph(self):
        graph = StateGraph(TaskGraphState)
        graph.add_node("round_dispatch", self._round_dispatch)
        graph.add_node("subtask_execution", self._subtask_execution)
        graph.add_node("context_update", self._context_update)
        graph.add_node("completion_judge", self._completion_judge)
        graph.add_node("human_intervention", self._human_intervention)

        graph.set_entry_point("round_dispatch")
        graph.add_conditional_edges(
            "round_dispatch",
            self._route_after_plan,
            {
                "subtask_execution": "subtask_execution",
                "completion_judge": "completion_judge",
                "human_intervention": "human_intervention",
            },
        )
        graph.add_conditional_edges(
            "subtask_execution",
            self._route_after_subtask,
            {
                "context_update": "context_update",
                "end": END,
            },
        )
        graph.add_edge("context_update", "completion_judge")
        graph.add_conditional_edges(
            "completion_judge",
            self._route_after_judge,
            {
                "round_dispatch": "round_dispatch",
                "human_intervention": "human_intervention",
                "end": END,
            },
        )
        graph.add_edge("human_intervention", END)
        return graph.compile()

    def _round_dispatch(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.DISPATCH_DECISION
        if task.loop_count >= task.max_loop_count:
            return {
                "task": task,
                "round_plan": RoundPlan(should_continue=True, reason="Loop limit exceeded"),
                "round_outputs": [],
                "paused": False,
            }

        agents = self.agent_registry.list_processing_agents()
        plan = plan_next_round_with_model(task, agents)
        if plan is None:
            require_system_mock_fallback_enabled("round_dispatch")
            plan = mock_round_plan(task, agents)
        if self._should_create_draft_human_gate(task, plan):
            plan = self._draft_human_gate_plan(task, plan)
        self._bind_planned_subtasks(task, plan)
        if plan.should_continue and plan.subtasks:
            task.loop_count += 1
            task.events.append(
                self._event("dispatch_decided", f"Round {task.loop_count}: {plan.reason or 'subtasks planned'}")
            )
        task.updated_at = utc_now()
        return {"task": task, "round_plan": plan, "round_outputs": [], "paused": False}

    @staticmethod
    def _bind_planned_subtasks(task: Task, plan: RoundPlan) -> None:
        execution_id = task.active_execution_id
        if execution_id is None:
            return
        round_index = task.loop_count + 1
        for ordinal, subtask in enumerate(plan.subtasks):
            logical_key = subtask.logical_key.strip()
            if not logical_key and subtask.id.strip():
                logical_key = subtask.id.strip()
            if not logical_key:
                identity = "\x00".join(
                    [
                        str(round_index),
                        str(ordinal),
                        subtask.title,
                        subtask.description,
                        subtask.assignee_type,
                    ]
                )
                digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
                logical_key = f"round_{round_index}_{ordinal}_{digest}"
            subtask.execution_id = execution_id
            subtask.logical_key = logical_key
            subtask.id = f"{task.id}_{execution_id}_{logical_key}"

    @staticmethod
    def _should_create_draft_human_gate(task: Task, plan: RoundPlan) -> bool:
        if task.loop_count > 0 or task.context.rounds:
            return False
        if plan.should_continue or plan.subtasks:
            return False
        return bool(task.draft and task.draft.suggested_assignee_type == "human")

    @staticmethod
    def _draft_human_gate_plan(task: Task, plan: RoundPlan) -> RoundPlan:
        title = task.draft.title if task.draft else task.title or "人工审核"
        description = task.draft.description if task.draft else task.description or task.content
        return RoundPlan(
            should_continue=True,
            execution_mode="sequential",
            reason=plan.reason or "意图识别要求人工审核",
            subtasks=[
                SubTask(
                    id=f"{task.id}_human_review",
                    title=title or "人工审核",
                    description=description or "请人工审核任务结果。",
                    assignee_type="human",
                )
            ],
        )

    def _route_after_plan(self, state: TaskGraphState) -> RouteAfterPlan:
        task = state["task"]
        plan = state["round_plan"]
        if task.loop_count >= task.max_loop_count and plan.should_continue:
            return "human_intervention"
        if plan.should_continue and plan.subtasks:
            return "subtask_execution"
        return "completion_judge"

    def _subtask_execution(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.SUBTASK_EXECUTION
        paused = False
        agents = self.agent_registry.list_processing_agents()
        agent_subtasks = []
        for subtask in state["round_plan"].subtasks:
            if subtask.assignee_type == "human":
                self._ensure_human_assignee(subtask, task)
                subtask.status = TaskStatus.RUNNING
                subtask.current_node = CurrentNode.HUMAN_EXECUTION
                paused = True
                task.events.append(self._event("human_task_created", f"{subtask.title}: waiting for human input"))
                continue
            if subtask.assignee_type == "condition":
                outcome = self._execute_condition_subtask(task, subtask)
                self._apply_subtask_outcome(task, subtask, None, outcome)
                continue
            agent_subtasks.append(subtask)

        if state["round_plan"].execution_mode == "parallel" and len(agent_subtasks) > 1:
            with ThreadPoolExecutor(max_workers=self.max_parallel_agent_subtasks) as executor:
                outcomes = list(executor.map(lambda item: self._run_agent_subtask(task, item, agents), agent_subtasks))
        else:
            outcomes = [self._run_agent_subtask(task, subtask, agents) for subtask in agent_subtasks]

        for subtask, agent, outcome in outcomes:
            self._apply_subtask_outcome(task, subtask, agent, outcome)
        failed_subtasks = [subtask for subtask in state["round_plan"].subtasks if subtask.status == TaskStatus.FAILED]
        if failed_subtasks:
            failure_message = self._format_failure_summary(failed_subtasks)
            self._append_pending_round(task, state["round_plan"])
            self.completion_service.finalize(
                task,
                candidate_status=TaskStatus.FAILED,
                output=failure_message,
                reason="One or more subtasks failed",
                decided_by_type="system",
                decided_by_id="task_graph",
            )
            task.events.append(self._event("task_failed", failure_message))
            return {"task": task, "round_plan": state["round_plan"], "round_outputs": [], "paused": False}
        outputs = [
            self._format_completed_subtask_context(subtask)
            for subtask in state["round_plan"].subtasks
            if subtask.status != TaskStatus.RUNNING and subtask.output
        ]
        if paused:
            self._append_pending_round(task, state["round_plan"])
            task.current_node = CurrentNode.HUMAN_EXECUTION
            task.events.append(self._event("human_task_waiting", f"Round {task.loop_count} is waiting for human input"))
        task.updated_at = utc_now()
        return {"task": task, "round_plan": state["round_plan"], "round_outputs": outputs, "paused": paused}

    def _run_agent_subtask(self, task: Task, subtask: SubTask, agents):
        agent = self._resolve_agent(subtask, task, agents)
        if agent:
            subtask.assigned_agent_id = agent.id
            subtask.assignee_type = "agent"
            subtask.current_node = CurrentNode.AGENT_EXECUTION
        outcome = self._execute_subtask(task, subtask, agent)
        return subtask, agent, outcome

    def _apply_subtask_outcome(self, task: Task, subtask: SubTask, agent, outcome: SubTaskExecutionOutcome) -> None:
        if agent:
            task.assigned_agent_id = agent.id
        subtask.output = outcome.output or outcome.error
        subtask.status = TaskStatus.SUCCEEDED if outcome.completed else TaskStatus.FAILED
        for ordinal, tool_result in enumerate(subtask.tool_results):
            self.artifact_service.register_tool_result(task, subtask, tool_result, ordinal=ordinal)
        if outcome.completed:
            self.artifact_service.register_subtask_output(task, subtask, subtask.output)
            event_type = "agent_executed" if agent else "human_node_processed"
            task.events.append(self._event(event_type, f"{subtask.title}: {subtask.output}"))
        else:
            task.events.append(self._event("subtask_failed", f"{subtask.title}: {subtask.output}"))

    def _route_after_subtask(self, state: TaskGraphState) -> RouteAfterSubTask:
        if state["task"].task_status != TaskStatus.RUNNING:
            return "end"
        if state["paused"]:
            return "end"
        return "context_update"

    def _context_update(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.CONTEXT_UPDATE
        plan = state["round_plan"]
        previous_summary = task.context.summary
        output_text = "\n".join(state["round_outputs"]).strip()
        round_item = TaskRound(
            round_index=task.loop_count,
            execution_mode=plan.execution_mode,
            reason=plan.reason,
            context_before=previous_summary,
            subtasks=plan.subtasks,
            context_after=self._build_context_summary(previous_summary, output_text),
        )
        task.context.rounds.append(round_item)
        task.context.summary = round_item.context_after
        task.events.append(self._event("context_updated", f"Round {task.loop_count} results merged into context"))
        task.updated_at = utc_now()
        return {"task": task, "round_plan": plan, "round_outputs": state["round_outputs"], "paused": False}

    def _completion_judge(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.COMPLETION_JUDGE
        plan = state["round_plan"]
        if not plan.should_continue or not plan.subtasks:
            final_output = plan.final_output or task.context.summary
            report = self.completion_service.finalize(
                task,
                candidate_status=TaskStatus.SUCCEEDED,
                output=final_output,
                reason=plan.reason or "Dispatcher found no remaining subtasks",
                criterion_results=self.completion_service.evaluate_criteria(task, final_output),
                decided_by_type="system",
                decided_by_id="task_graph",
            )
            task.events.append(
                self._event("completion_judged", f"Dispatcher finalized task as {report.terminal_status.value}")
            )
        else:
            task.events.append(self._event("completion_judged", "Dispatcher requested another round check"))
        if task.task_status == TaskStatus.RUNNING:
            task.updated_at = utc_now()
        return {"task": task, "round_plan": plan, "round_outputs": state["round_outputs"], "paused": False}

    def _route_after_judge(self, state: TaskGraphState) -> RouteAfterJudge:
        task = state["task"]
        if task.current_node == CurrentNode.HUMAN_INTERVENTION:
            return "end"
        if task.task_status != TaskStatus.RUNNING:
            return "end"
        if task.loop_count >= task.max_loop_count:
            return "human_intervention"
        return "round_dispatch"

    def _human_intervention(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.HUMAN_INTERVENTION
        task.events.append(self._event("human_intervention_required", "Loop limit exceeded"))
        task.updated_at = utc_now()
        return {"task": task, "round_plan": state["round_plan"], "round_outputs": state["round_outputs"], "paused": False}

    def _resolve_agent(self, subtask: SubTask, task: Task, agents):
        if subtask.assigned_agent_id:
            matched = next((agent for agent in agents if agent.id == subtask.assigned_agent_id), None)
            if matched:
                return matched
        probe_task = task.model_copy(update={"title": subtask.title, "description": subtask.description})
        require_system_mock_fallback_enabled("agent_resolution")
        return mock_dispatch(probe_task, agents)

    def _execute_subtask(self, task: Task, subtask: SubTask, agent) -> SubTaskExecutionOutcome:
        if agent:
            execution_result = execute_subtask_with_tools_model(task, subtask, agent, [])
            if execution_result is None:
                require_system_mock_fallback_enabled("agent_execution")
                return SubTaskExecutionOutcome(completed=True, output=mock_agent_execution(task, agent))
            tool_calls, output = execution_result
            if tool_calls:
                subtask.tool_calls = tool_calls
                subtask.tool_results = [self.tool_executor.execute(agent, tool_call) for tool_call in tool_calls]
                followup_result = execute_subtask_with_tools_model(task, subtask, agent, subtask.tool_results)
                if followup_result is None:
                    require_system_mock_fallback_enabled("agent_execution_followup")
                    return SubTaskExecutionOutcome(completed=True, output=mock_agent_execution(task, agent))
                tool_calls, output = followup_result
                if tool_calls:
                    subtask.tool_calls.extend(tool_calls)
            failed_tools = [result for result in subtask.tool_results if not result.success]
            if failed_tools:
                error = "; ".join(result.error or f"Tool {result.tool_name} failed" for result in failed_tools)
                return SubTaskExecutionOutcome(completed=False, error=error)
            if not output:
                return SubTaskExecutionOutcome(completed=False, error="Agent returned no output")
            return SubTaskExecutionOutcome(completed=True, output=output)
        require_system_mock_fallback_enabled("human_node_processing")
        return SubTaskExecutionOutcome(completed=True, output=mock_human_node_processing(task))

    def _execute_condition_subtask(self, task: Task, subtask: SubTask) -> SubTaskExecutionOutcome:
        config = subtask.result_metadata.get("config", {})
        source_node_id = str(config.get("source_node_id", "")).strip()
        field = str(config.get("field", "decision")).strip() or "decision"
        default_decision = str(config.get("default_decision", "unknown")).strip() or "unknown"
        allowed_decisions = config.get("allowed_decisions", [])
        source_subtask = self._find_completed_workflow_subtask(task, source_node_id) if source_node_id else None
        source_value = source_subtask.result_metadata.get(field) if source_subtask else None
        decision = str(source_value or default_decision)
        if isinstance(allowed_decisions, list) and allowed_decisions and decision not in allowed_decisions:
            decision = default_decision
        subtask.result_metadata = {
            "decision": decision,
            "reason": f"Matched {source_node_id}.{field}" if source_subtask and source_value is not None else "Used default decision",
            "source_node_id": source_node_id,
            "source_value": source_value,
        }
        subtask.current_node = CurrentNode.SUBTASK_EXECUTION
        return SubTaskExecutionOutcome(completed=True, output=f"Condition decision: {decision}")

    @staticmethod
    def _find_completed_workflow_subtask(task: Task, node_id: str) -> SubTask | None:
        if not node_id:
            return None
        expected_id = f"{task.id}_{node_id}"
        for round_item in task.context.rounds:
            for subtask in round_item.subtasks:
                matches = (
                    subtask.logical_key == node_id
                    or subtask.id == expected_id
                )
                if matches and subtask.status == TaskStatus.SUCCEEDED:
                    return subtask
        return None

    @staticmethod
    def _format_subtask_context(subtask: SubTask, outcome: SubTaskExecutionOutcome) -> str:
        if outcome.completed:
            return outcome.context_text
        return f"FAILED: {subtask.title}\nReason: {outcome.context_text}"

    @staticmethod
    def _format_completed_subtask_context(subtask: SubTask) -> str:
        if subtask.status == TaskStatus.FAILED:
            return f"FAILED: {subtask.title}\nReason: {subtask.output}"
        return subtask.output

    @staticmethod
    def _append_pending_round(task: Task, plan: RoundPlan) -> None:
        if any(round_item.round_index == task.loop_count for round_item in task.context.rounds):
            return
        task.context.rounds.append(
            TaskRound(
                round_index=task.loop_count,
                execution_mode=plan.execution_mode,
                reason=plan.reason,
                context_before=task.context.summary,
                subtasks=plan.subtasks,
                context_after=task.context.summary,
            )
        )

    @staticmethod
    def _build_context_summary(previous_summary: str, output_text: str) -> str:
        if previous_summary and output_text:
            return f"{previous_summary}\n{output_text}"
        return output_text or previous_summary

    @staticmethod
    def _event(event_type: str, message: str) -> Event:
        return Event(type=event_type, message=message, created_at=utc_now())

    def _ensure_human_assignee(self, subtask: SubTask, task: Task | None = None) -> None:
        if self._has_valid_subtask_assignee(subtask):
            subtask.assignee_role = subtask.assignee_role or "approver"
            return

        default_assignee = self._default_human_assignee_from_task(task)
        if default_assignee:
            subtask.assignee_user_id = default_assignee["assignee_user_id"]
            subtask.assignee_user_name = default_assignee["assignee_user_name"]
            subtask.assignee_role = default_assignee["assignee_role"]
            return

        if self.user_registry is not None:
            inferred = self._infer_user_assignee_from_text(subtask, task)
            if inferred:
                subtask.assignee_user_id = inferred.id
                subtask.assignee_user_name = inferred.name
                subtask.assignee_role = subtask.assignee_role or "approver"
                return
            self._assign_root_user(subtask)
            return

        if subtask.assignee_user_id and subtask.assignee_user_name:
            subtask.assignee_role = subtask.assignee_role or "approver"
            return

        subtask.assignee_user_id = subtask.assignee_user_id or "root"
        subtask.assignee_user_name = subtask.assignee_user_name or "管理员"
        subtask.assignee_role = subtask.assignee_role or "admin"

    def _has_valid_subtask_assignee(self, subtask: SubTask) -> bool:
        if not subtask.assignee_user_id or not subtask.assignee_user_name:
            return False
        if self.user_registry is None:
            return True
        user = self.user_registry.get_user(subtask.assignee_user_id)
        return bool(user and user.status == "active")

    def _default_human_assignee_from_task(self, task: Task | None) -> dict | None:
        if task is None:
            return None
        raw_assignee = task.request_metadata.get("default_human_assignee")
        if not isinstance(raw_assignee, dict):
            return None
        assignee_user_id = str(raw_assignee.get("assignee_user_id") or "").strip()
        assignee_user_name = str(raw_assignee.get("assignee_user_name") or "").strip()
        assignee_role = str(raw_assignee.get("assignee_role") or "").strip()
        if not assignee_user_id or not assignee_user_name:
            return None
        if self.user_registry is not None:
            user = self.user_registry.get_user(assignee_user_id)
            if not user or user.status != "active":
                return None
            assignee_user_name = user.name
            assignee_role = user.role.value
        return {
            "assignee_user_id": assignee_user_id,
            "assignee_user_name": assignee_user_name,
            "assignee_role": assignee_role or ("admin" if assignee_user_id == "root" else "approver"),
        }

    def _infer_user_assignee_from_text(self, subtask: SubTask, task: Task | None) -> User | None:
        if self.user_registry is None:
            return None
        text_parts = [subtask.title, subtask.description]
        if task:
            text_parts.extend([task.title or "", task.description or "", task.content])
        haystack = "\n".join(part for part in text_parts if part)
        candidates = [user for user in self.user_registry.list_users() if user.id != "root" and user.name]
        candidates.sort(key=lambda user: len(user.name), reverse=True)
        for user in candidates:
            if user.name in haystack:
                return user
        return None

    def _assign_root_user(self, subtask: SubTask) -> None:
        root_user = self.user_registry.get_user("root") if self.user_registry is not None else None
        subtask.assignee_user_id = root_user.id if root_user else "root"
        subtask.assignee_user_name = root_user.name if root_user else "管理员"
        subtask.assignee_role = subtask.assignee_role or "admin"

    @staticmethod
    def _format_failure_summary(failed_subtasks: list[SubTask]) -> str:
        return "\n".join(
            f"{subtask.title}: {subtask.output or '子任务执行失败'}" for subtask in failed_subtasks
        )
