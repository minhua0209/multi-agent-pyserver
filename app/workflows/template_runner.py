from __future__ import annotations

from app.core.enums import CurrentNode, TaskStatus
from app.core.models import RoundPlan, SubTask, Task, WorkflowNode, WorkflowTemplate, scoped_subtask_id
from app.services.completion_service import CompletionService
from app.services.artifact_service import ArtifactService
from app.services.storage import AgentRegistry
from app.workflows.task_graph import TaskGraphRunner


class WorkflowTemplateRunner:
    def __init__(
        self,
        agent_registry: AgentRegistry,
        completion_service: CompletionService | None = None,
        artifact_service: ArtifactService | None = None,
    ) -> None:
        self.artifact_service = (
            artifact_service
            or (completion_service.artifact_service if completion_service else None)
            or ArtifactService()
        )
        self.completion_service = completion_service or CompletionService(
            artifact_service=self.artifact_service
        )
        self.task_graph = TaskGraphRunner(
            agent_registry,
            completion_service=self.completion_service,
            artifact_service=self.artifact_service,
        )

    def run(self, task: Task, workflow: WorkflowTemplate) -> Task:
        if task.current_node.value == "human_execution" and self._has_running_human_subtasks(task):
            return task

        completed_node_ids = self._completed_node_ids(task)
        node_map = {node.id: node for node in workflow.definition.nodes}
        while True:
            if task.task_status != TaskStatus.RUNNING:
                return task
            ready_nodes = self._ready_nodes(workflow, completed_node_ids, task)
            runnable_nodes = [node for node in ready_nodes if node.type in {"agent", "human", "condition"}]
            if not runnable_nodes:
                if ready_nodes and all(node.type == "end" for node in ready_nodes):
                    return self._complete_workflow(task, workflow, ready_nodes[0].id)
                return self._mark_workflow_blocked(task, workflow, completed_node_ids)

            plan = RoundPlan(
                should_continue=True,
                execution_mode="parallel" if len(runnable_nodes) > 1 else "sequential",
                reason=f"Workflow {workflow.id} ready nodes",
                subtasks=[self._node_to_subtask(task, node) for node in runnable_nodes],
            )
            task = self._run_round(task, plan)
            if task.task_status != TaskStatus.RUNNING:
                return task
            if task.current_node.value == "human_execution":
                return task
            completed_node_ids = self._completed_node_ids(task)
            ready_after_round = self._ready_nodes(workflow, completed_node_ids, task)
            if ready_after_round and all(node.type == "end" for node in ready_after_round):
                return self._complete_workflow(task, workflow, ready_after_round[0].id)

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
        if state["task"].task_status == TaskStatus.FAILED:
            return state["task"]
        if state["paused"]:
            return state["task"]
        return runner._context_update(state)["task"]

    def _complete_workflow(self, task: Task, workflow: WorkflowTemplate, end_node_id: str) -> Task:
        final_output = task.context.summary
        report = self.completion_service.finalize(
            task,
            candidate_status=TaskStatus.SUCCEEDED,
            output=final_output,
            reason=f"Workflow {workflow.id} reached end node",
            criterion_results=self.completion_service.evaluate_criteria(task, final_output),
            workflow_end_reached=True,
            workflow_end_node_id=end_node_id,
            decided_by_type="system",
            decided_by_id="workflow_template_runner",
        )
        task.events.append(
            self.task_graph._event("workflow_completed", f"Workflow {workflow.id} finalized as {report.terminal_status.value}")
        )
        return task

    def _mark_workflow_blocked(
        self,
        task: Task,
        workflow: WorkflowTemplate,
        completed_node_ids: set[str],
    ) -> Task:
        pending_nodes = [
            node.title or node.id
            for node in workflow.definition.nodes
            if node.type not in {"start"} and node.id not in completed_node_ids
        ]
        pending_text = "、".join(pending_nodes[:6]) if pending_nodes else "未知节点"
        message = f"Workflow {workflow.id} 没有可继续执行的节点，且未到达完成节点。待处理节点：{pending_text}"
        self.completion_service.finalize(
            task,
            candidate_status=TaskStatus.BLOCKED,
            output=message,
            reason=message,
            workflow_end_reached=False,
            decided_by_type="system",
            decided_by_id="workflow_template_runner",
        )
        task.events.append(self.task_graph._event("workflow_blocked", message))
        return task

    @staticmethod
    def _node_to_subtask(task: Task, node: WorkflowNode) -> SubTask:
        assignee = WorkflowTemplateRunner._human_assignee_from_config(node.config, task) if node.type == "human" else {}
        execution_id = task.active_execution_id or ""
        subtask = SubTask(
            id=(
                scoped_subtask_id(task.id, execution_id, node.id)
                if execution_id
                else scoped_subtask_id(task.id, "", node.id)
            ),
            execution_id=execution_id,
            logical_key=node.id,
            title=node.title or node.id,
            description=WorkflowTemplateRunner._node_description(node),
            assigned_agent_id=node.agent_id,
            assignee_type=node.type if node.type in {"human", "condition"} else "agent",
            **assignee,
        )
        if node.type == "condition":
            subtask.result_metadata = {"config": node.config}
        return subtask

    @staticmethod
    def _node_description(node: WorkflowNode) -> str:
        if node.type == "human":
            handoff_instruction = str(node.config.get("handoff_instruction") or "").strip()
            if handoff_instruction:
                return handoff_instruction
        return node.description or node.title or node.id

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
                    completed.add(
                        WorkflowTemplateRunner._subtask_logical_key(task, subtask)
                    )
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
        has_start_node = any(node.type == "start" for node in workflow.definition.nodes)
        incoming = {node.id: [] for node in workflow.definition.nodes}
        outgoing = {node.id: [] for node in workflow.definition.nodes}
        for edge in workflow.definition.edges:
            incoming.setdefault(edge.to_node, []).append(edge)
            outgoing.setdefault(edge.from_node, []).append(edge.to_node)

        ready = []
        for node in workflow.definition.nodes:
            if node.id in completed_node_ids or node.type == "start":
                continue
            incoming_edges = incoming.get(node.id, [])
            valid_incoming_edges = [edge for edge in incoming_edges if edge.from_node in node_map]
            if has_start_node and not valid_incoming_edges:
                continue
            dependencies = [edge for edge in valid_incoming_edges if node_map[edge.from_node].type != "start"]
            if WorkflowTemplateRunner._dependencies_ready(dependencies, completed_node_ids, completed_subtasks):
                ready.append(node)
        return ready

    @staticmethod
    def _completed_subtasks_by_node_id(task: Task) -> dict[str, SubTask]:
        completed = {}
        for round_item in task.context.rounds:
            for subtask in round_item.subtasks:
                if subtask.status != TaskStatus.SUCCEEDED:
                    continue
                node_id = WorkflowTemplateRunner._subtask_logical_key(task, subtask)
                completed[node_id] = subtask
        return completed

    @staticmethod
    def _subtask_logical_key(task: Task, subtask: SubTask) -> str:
        if subtask.logical_key:
            return subtask.logical_key
        if subtask.execution_id:
            execution_prefix = f"{task.id}_{subtask.execution_id}_"
            if subtask.id.startswith(execution_prefix):
                return subtask.id[len(execution_prefix) :]
        task_prefix = f"{task.id}_"
        return (
            subtask.id[len(task_prefix) :]
            if subtask.id.startswith(task_prefix)
            else subtask.id
        )

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
