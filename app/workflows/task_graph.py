from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.core.enums import CurrentNode, TaskStatus
from app.core.mock_llm import mock_dispatch, mock_human_node_processing, mock_round_plan
from app.core.model_client import execute_subtask_with_tools_model, plan_next_round_with_model
from app.core.models import Event, RoundPlan, SubTask, Task, TaskRound, utc_now
from app.services.storage import AgentRegistry
from app.services.tool_executor import ToolExecutor


class TaskGraphState(TypedDict):
    task: Task
    round_plan: RoundPlan
    round_outputs: list[str]


RouteAfterPlan = Literal["subtask_execution", "completion_judge", "human_intervention"]
RouteAfterJudge = Literal["round_dispatch", "human_intervention", "end"]


class TaskGraphRunner:
    def __init__(self, agent_registry: AgentRegistry) -> None:
        self.agent_registry = agent_registry
        self.tool_executor = ToolExecutor()
        self.graph = self._build_graph()

    def run(self, task: Task) -> Task:
        final_state = self.graph.invoke(
            {
                "task": task,
                "round_plan": RoundPlan(should_continue=False),
                "round_outputs": [],
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
        graph.add_edge("subtask_execution", "context_update")
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
            return {"task": task, "round_plan": RoundPlan(should_continue=False), "round_outputs": []}

        agents = self.agent_registry.list_agents()
        plan = plan_next_round_with_model(task, agents) or mock_round_plan(task, agents)
        if plan.should_continue and plan.subtasks:
            task.loop_count += 1
            task.events.append(
                self._event("dispatch_decided", f"Round {task.loop_count}: {plan.reason or 'subtasks planned'}")
            )
        task.updated_at = utc_now()
        return {"task": task, "round_plan": plan, "round_outputs": []}

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
        outputs = []
        agents = self.agent_registry.list_agents()
        for subtask in state["round_plan"].subtasks:
            agent = self._resolve_agent(subtask, task, agents)
            if agent:
                subtask.assigned_agent_id = agent.id
                task.assigned_agent_id = agent.id
            output = self._execute_subtask(task, subtask, agent)
            subtask.output = output
            subtask.status = TaskStatus.SUCCEEDED
            outputs.append(output)
            event_type = "agent_executed" if agent else "human_node_processed"
            task.events.append(self._event(event_type, f"{subtask.title}: {output}"))
        task.updated_at = utc_now()
        return {"task": task, "round_plan": state["round_plan"], "round_outputs": outputs}

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
        return {"task": task, "round_plan": plan, "round_outputs": state["round_outputs"]}

    def _completion_judge(self, state: TaskGraphState) -> TaskGraphState:
        task = state["task"]
        task.current_node = CurrentNode.COMPLETION_JUDGE
        plan = state["round_plan"]
        if not plan.should_continue or not plan.subtasks:
            task.task_status = TaskStatus.SUCCEEDED
            task.final_output = plan.final_output or task.context.summary
            task.events.append(self._event("completion_judged", "Dispatcher found no remaining subtasks"))
        else:
            task.events.append(self._event("completion_judged", "Dispatcher requested another round check"))
        task.updated_at = utc_now()
        return {"task": task, "round_plan": plan, "round_outputs": state["round_outputs"]}

    def _route_after_judge(self, state: TaskGraphState) -> RouteAfterJudge:
        task = state["task"]
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
        return {"task": task, "round_plan": state["round_plan"], "round_outputs": state["round_outputs"]}

    def _resolve_agent(self, subtask: SubTask, task: Task, agents):
        if subtask.assigned_agent_id:
            matched = next((agent for agent in agents if agent.id == subtask.assigned_agent_id), None)
            if matched:
                return matched
        probe_task = task.model_copy(update={"title": subtask.title, "description": subtask.description})
        return mock_dispatch(probe_task, agents)

    def _execute_subtask(self, task: Task, subtask: SubTask, agent) -> str:
        if agent:
            tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, [])
            if tool_calls:
                subtask.tool_calls = tool_calls
                subtask.tool_results = [self.tool_executor.execute(agent, tool_call) for tool_call in tool_calls]
                tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, subtask.tool_results)
                if tool_calls:
                    subtask.tool_calls.extend(tool_calls)
            return output or f"{agent.name} completed subtask {subtask.id}: {subtask.title}"
        return mock_human_node_processing(task)

    @staticmethod
    def _build_context_summary(previous_summary: str, output_text: str) -> str:
        if previous_summary and output_text:
            return f"{previous_summary}\n{output_text}"
        return output_text or previous_summary

    @staticmethod
    def _event(event_type: str, message: str) -> Event:
        return Event(type=event_type, message=message, created_at=utc_now())
