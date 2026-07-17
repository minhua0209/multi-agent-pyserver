import { Task, WorkflowDefinition } from "./api/taskhub"
import { isManualWorkflowTask, taskTypeText } from "./taskType"
import { WorkflowReactFlowEdge, WorkflowReactFlowNode, workflowToReactFlow } from "./workflowReactFlow"

export interface TaskDetailSummaryBlock {
  key: "request" | "draft"
  title: string
  text: string
}

export function taskDetailSummaryBlocks(task: Task): TaskDetailSummaryBlock[] {
  return [
    {
      key: "request",
      title: "原始诉求",
      text: task.content || task.description || "-",
    },
    {
      key: "draft",
      title: "任务清单",
      text: draftTaskListText(task),
    },
  ]
}

export function taskDetailTypeBadge(task: Task) {
  return {
    text: taskTypeText(task),
    color: isManualWorkflowTask(task) ? "purple" : "blue",
  }
}

export function manualWorkflowFlowElements(
  task: Task,
  definition: WorkflowDefinition,
): { nodes: WorkflowReactFlowNode[]; edges: WorkflowReactFlowEdge[] } {
  const flow = workflowToReactFlow(definition)
  return {
    nodes: flow.nodes.map((node) => {
      const sourceNode = definition.nodes.find((item) => item.id === node.id)
      const subtask = workflowSubtaskForNode(task, node.id)
      const status = sourceNode ? workflowNodeState(task, sourceNode) : "pending"
      return {
        ...node,
        draggable: false,
        selectable: false,
        data: {
          ...node.data,
          status,
          statusText: workflowNodeStateText(status),
          kindText: workflowNodeKindText(sourceNode?.type),
          output: subtask?.output || "",
          assigneeUserName: subtask?.assignee_user_name || node.data.assigneeUserName || "",
        },
        className: `task-detail-flow-node ${status}`,
      }
    }),
    edges: flow.edges.map((edge) => ({
      ...edge,
      selectable: false,
      animated: false,
      style: {
        stroke: "#0f8ca8",
        strokeWidth: 2.2,
      },
    })),
  }
}

export function workflowNodeKindText(type?: string) {
  return { start: "开始", end: "完成", agent: "Agent", human: "人工", condition: "条件" }[type || ""] || type || "节点"
}

export function workflowNodeStateText(state: string) {
  return { pending: "未开始", running: "执行中", succeeded: "已完成", failed: "失败" }[state] || state
}

export function workflowNodeStateColor(state: string) {
  return { pending: "default", running: "processing", succeeded: "success", failed: "error" }[state] || "default"
}

export function workflowSubtaskForNode(task: Task, nodeId: string) {
  const prefix = `${task.id}_`
  return (task.context?.rounds || [])
    .flatMap((round) => round.subtasks || [])
    .find((subtask) => {
      const subtaskNodeId = subtask.id.startsWith(prefix) ? subtask.id.slice(prefix.length) : subtask.id
      return subtaskNodeId === nodeId
    })
}

function workflowNodeState(task: Task, node: WorkflowDefinition["nodes"][number]) {
  if (node.type === "start") return "succeeded"
  if (node.type === "end") return taskStatus(task) === "succeeded" ? "succeeded" : "pending"
  return workflowSubtaskForNode(task, node.id)?.status || "pending"
}

function taskStatus(task: Task) {
  return task.task_status || task.status || "running"
}

function draftTaskListText(task: Task) {
  if (!task.draft) return "暂无识别任务清单"
  const title = task.draft.title || "未命名任务"
  const description = task.draft.description || ""
  return description ? `${title}\n${description}` : title
}
