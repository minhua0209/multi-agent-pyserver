import { SubTask, Task, WorkflowDefinition } from "./api/taskhub"
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
  const flow = workflowToReactFlow(definition, detailWorkflowNodePositions(definition))
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

export function detailWorkflowNodePositions(definition: WorkflowDefinition) {
  const orderedNodes = orderedWorkflowNodes(definition)
  return Object.fromEntries(
    orderedNodes.map((node, index) => {
      const column = index % 3
      const row = Math.floor(index / 3)
      return [
        node.id,
        {
          left: 80 + column * 340,
          top: 80 + row * 210,
        },
      ]
    }),
  )
}

function orderedWorkflowNodes(definition: WorkflowDefinition) {
  const nodeById = new Map(definition.nodes.map((node) => [node.id, node]))
  const outgoing = new Map<string, string[]>()
  const incomingCount = new Map<string, number>()
  definition.nodes.forEach((node) => {
    outgoing.set(node.id, [])
    incomingCount.set(node.id, 0)
  })
  definition.edges.forEach((edge) => {
    if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) return
    outgoing.get(edge.from)?.push(edge.to)
    incomingCount.set(edge.to, (incomingCount.get(edge.to) || 0) + 1)
  })

  const startIds = definition.nodes
    .filter((node) => node.id === "start" || (incomingCount.get(node.id) || 0) === 0)
    .map((node) => node.id)
  const queue = startIds.length ? [...startIds] : definition.nodes.map((node) => node.id)
  const seen = new Set<string>()
  const ordered: WorkflowDefinition["nodes"] = []

  while (queue.length) {
    const nodeId = queue.shift()!
    if (seen.has(nodeId)) continue
    const node = nodeById.get(nodeId)
    if (!node) continue
    seen.add(nodeId)
    ordered.push(node)
    for (const target of outgoing.get(nodeId) || []) {
      if (!seen.has(target)) queue.push(target)
    }
  }

  definition.nodes.forEach((node) => {
    if (!seen.has(node.id)) ordered.push(node)
  })
  return ordered
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

export function compactContextText(value: unknown, maxLength = 96) {
  const text = displayContextValue(value).replace(/\s+/g, " ").trim()
  if (!text) return ""
  if (text.length <= maxLength) return text
  return `${text.slice(0, Math.max(0, maxLength - 3)).trim()}...`
}

export function taskContextNodeView(subtask: SubTask) {
  const output = String(subtask.output || "").trim()
  const description = String(subtask.description || "").trim()
  const error = String(subtask.error_message || "").trim()
  const preview = compactContextText(output || description || error || "暂无摘要", 86)
  return {
    title: subtask.title || subtask.id,
    typeText: subtask.assignee_type === "human" || subtask.current_node === "human"
      ? "人工"
      : subtask.assignee_type === "condition"
        ? "条件"
        : "Agent",
    assigneeText: subtask.assignee_user_name || subtask.assigned_agent_id || "",
    preview,
    hasDetail: Boolean(description || output || error || subtask.tool_results?.length),
  }
}

function displayContextValue(value: unknown) {
  if (value === undefined || value === null) return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
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
