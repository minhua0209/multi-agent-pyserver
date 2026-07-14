from __future__ import annotations

from app.core.enums import CurrentNode, TaskStatus
from app.core.models import RoundPlan, SubTask, Task, WorkflowNode, WorkflowTemplate
from app.services.storage import AgentRegistry
from app.workflows.task_graph import TaskGraphRunner


class WorkflowTemplateRunner:
    def __init__(self, agent_registry: AgentRegistry) -> None:
        self.task_graph = TaskGraphRunner(agent_registry)

    def run(self, task: Task, workflow: WorkflowTemplate) -> Task:
        if task.current_node.value == "human_execution" and self._has_running_human_subtasks(task):
            return task

        completed_node_ids = self._completed_node_ids(task)
        node_map = {node.id: node for node in workflow.definition.nodes}
        while True:
            ready_nodes = self._ready_nodes(workflow, completed_node_ids)
            runnable_nodes = [node for node in ready_nodes if node.type in {"agent", "human"}]
            if not runnable_nodes:
                task.task_status = TaskStatus.SUCCEEDED
                task.current_node = CurrentNode.COMPLETION_JUDGE
                task.final_output = task.context.summary
                task.events.append(self.task_graph._event("workflow_completed", f"Workflow {workflow.id} completed"))
                return task

            plan = RoundPlan(
                should_continue=True,
                execution_mode="parallel" if len(runnable_nodes) > 1 else "sequential",
                reason=f"Workflow {workflow.id} ready nodes",
                subtasks=[self._node_to_subtask(task, node) for node in runnable_nodes],
            )
            task = self._run_round(task, plan)
            if task.current_node.value == "human_execution":
                return task
            completed_node_ids = self._completed_node_ids(task)
            if all(node.type == "end" for node in self._ready_nodes(workflow, completed_node_ids)):
                task.task_status = TaskStatus.SUCCEEDED
                task.current_node = CurrentNode.COMPLETION_JUDGE
                task.final_output = task.context.summary
                task.events.append(self.task_graph._event("workflow_completed", f"Workflow {workflow.id} completed"))
                return task

    def _run_round(self, task: Task, plan: RoundPlan) -> Task:
        runner = self.task_graph
        task.loop_count += 1
        task.events.append(
            runner._event("workflow_dispatch_decided", f"Round {task.loop_count}: workflow nodes planned")
        )
        state = runner._subtask_execution(
            {
                "task": task,
                "round_plan": plan,
                "round_outputs": [],
                "paused": False,
            }
        )
        if state["paused"]:
            return state["task"]
        return runner._context_update(state)["task"]

    @staticmethod
    def _node_to_subtask(task: Task, node: WorkflowNode) -> SubTask:
        return SubTask(
            id=f"{task.id}_{node.id}",
            title=node.title or node.id,
            description=node.description or node.title or node.id,
            assigned_agent_id=node.agent_id,
            assignee_type="human" if node.type == "human" else "agent",
        )

    @staticmethod
    def _completed_node_ids(task: Task) -> set[str]:
        completed = set()
        for round_item in task.context.rounds:
            for subtask in round_item.subtasks:
                if subtask.status == TaskStatus.SUCCEEDED:
                    prefix = f"{task.id}_"
                    completed.add(subtask.id[len(prefix) :] if subtask.id.startswith(prefix) else subtask.id)
        return completed

    @staticmethod
    def _has_running_human_subtasks(task: Task) -> bool:
        for round_item in task.context.rounds:
            for subtask in round_item.subtasks:
                if subtask.assignee_type == "human" and subtask.status == TaskStatus.RUNNING:
                    return True
        return False

    @staticmethod
    def _ready_nodes(workflow: WorkflowTemplate, completed_node_ids: set[str]) -> list[WorkflowNode]:
        node_map = {node.id: node for node in workflow.definition.nodes}
        incoming = {node.id: [] for node in workflow.definition.nodes}
        outgoing = {node.id: [] for node in workflow.definition.nodes}
        for edge in workflow.definition.edges:
            incoming.setdefault(edge.to_node, []).append(edge.from_node)
            outgoing.setdefault(edge.from_node, []).append(edge.to_node)

        ready = []
        for node in workflow.definition.nodes:
            if node.id in completed_node_ids or node.type == "start":
                continue
            dependencies = incoming.get(node.id, [])
            normalized_dependencies = [item for item in dependencies if node_map.get(item) and node_map[item].type != "start"]
            if all(item in completed_node_ids for item in normalized_dependencies):
                ready.append(node)
        return ready
