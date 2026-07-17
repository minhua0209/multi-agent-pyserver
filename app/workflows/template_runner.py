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
            ready_nodes = self._ready_nodes(workflow, completed_node_ids, task)
            runnable_nodes = [node for node in ready_nodes if node.type in {"agent", "human", "condition"}]
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
            if all(node.type == "end" for node in self._ready_nodes(workflow, completed_node_ids, task)):
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
        assignee = WorkflowTemplateRunner._human_assignee_from_config(node.config, task) if node.type == "human" else {}
        subtask = SubTask(
            id=f"{task.id}_{node.id}",
            title=node.title or node.id,
            description=node.description or node.title or node.id,
            assigned_agent_id=node.agent_id,
            assignee_type=node.type if node.type in {"human", "condition"} else "agent",
            **assignee,
        )
        if node.type == "condition":
            subtask.result_metadata = {"config": node.config}
        return subtask

    @staticmethod
    def _human_assignee_from_config(config: dict, task: Task) -> dict:
        default_assignee = task.request_metadata.get("default_human_assignee")
        if not config.get("assignee_user_id") and isinstance(default_assignee, dict):
            return {
                "assignee_user_id": str(default_assignee.get("assignee_user_id") or "root"),
                "assignee_user_name": str(default_assignee.get("assignee_user_name") or "管理员"),
                "assignee_role": str(default_assignee.get("assignee_role") or "admin"),
            }
        return {
            "assignee_user_id": str(config.get("assignee_user_id") or "root"),
            "assignee_user_name": str(config.get("assignee_user_name") or "管理员"),
            "assignee_role": str(config.get("assignee_role") or "admin"),
        }

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
    def _ready_nodes(workflow: WorkflowTemplate, completed_node_ids: set[str], task: Task) -> list[WorkflowNode]:
        node_map = {node.id: node for node in workflow.definition.nodes}
        completed_subtasks = WorkflowTemplateRunner._completed_subtasks_by_node_id(task)
        incoming = {node.id: [] for node in workflow.definition.nodes}
        outgoing = {node.id: [] for node in workflow.definition.nodes}
        for edge in workflow.definition.edges:
            incoming.setdefault(edge.to_node, []).append(edge)
            outgoing.setdefault(edge.from_node, []).append(edge.to_node)

        ready = []
        for node in workflow.definition.nodes:
            if node.id in completed_node_ids or node.type == "start":
                continue
            dependencies = [
                edge for edge in incoming.get(node.id, []) if node_map.get(edge.from_node) and node_map[edge.from_node].type != "start"
            ]
            if WorkflowTemplateRunner._dependencies_ready(dependencies, completed_node_ids, completed_subtasks):
                ready.append(node)
        return ready

    @staticmethod
    def _completed_subtasks_by_node_id(task: Task) -> dict[str, SubTask]:
        completed = {}
        prefix = f"{task.id}_"
        for round_item in task.context.rounds:
            for subtask in round_item.subtasks:
                if subtask.status != TaskStatus.SUCCEEDED:
                    continue
                node_id = subtask.id[len(prefix) :] if subtask.id.startswith(prefix) else subtask.id
                completed[node_id] = subtask
        return completed

    @staticmethod
    def _dependencies_ready(edges, completed_node_ids: set[str], completed_subtasks: dict[str, SubTask]) -> bool:
        if not edges:
            return True
        conditional_edges = [edge for edge in edges if edge.condition]
        if conditional_edges:
            return any(
                edge.from_node in completed_node_ids
                and WorkflowTemplateRunner._condition_matches(edge.condition, completed_subtasks.get(edge.from_node))
                for edge in conditional_edges
            )
        return all(edge.from_node in completed_node_ids for edge in edges)

    @staticmethod
    def _condition_matches(condition: dict, source_subtask: SubTask | None) -> bool:
        if not condition:
            return True
        if source_subtask is None:
            return False
        if condition.get("type") == "decision":
            return source_subtask.result_metadata.get("decision") == condition.get("value")
        field = condition.get("field")
        if not isinstance(field, str) or not field:
            return False
        data = source_subtask.result_metadata
        actual = data.get(field)
        operator = condition.get("operator", "eq")
        expected = condition.get("value")
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "in":
            return isinstance(expected, list) and actual in expected
        if operator == "not_in":
            return isinstance(expected, list) and actual not in expected
        if operator == "exists":
            return field in data
        if operator == "not_exists":
            return field not in data
        if operator == "contains":
            return isinstance(actual, (list, str)) and expected in actual
        return False
