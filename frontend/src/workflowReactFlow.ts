import { Edge, Node, XYPosition } from "@xyflow/react"

import { WorkflowDefinition, WorkflowEdge, WorkflowNode } from "./api/taskhub"
import { WorkflowNodePositions, defaultWorkflowNodePositions } from "./workflowCanvas"

export interface WorkflowNodeData extends Record<string, unknown> {
  id: string
  title: string
  description: string
  kind: string
  agentId?: string | null
  instruction: string
  assignee?: string
  handoffInstruction?: string
  conditionDescription?: string
  conditionContent?: string
  editing?: boolean
  onConfigChange?: (nodeId: string, patch: Record<string, unknown>) => void
  onEditStart?: (nodeId: string) => void
  onEditEnd?: () => void
}

export type WorkflowReactFlowNode = Node<WorkflowNodeData, "workflowNode">
export type WorkflowReactFlowEdge = Edge<Record<string, unknown>>

export interface WorkflowNodeDetailItem {
  label: string
  value: string
}

export interface WorkflowNodeInlineEditField {
  key: string
  label: string
  inputType: "input" | "textarea"
  value: string
  placeholder: string
}

export function workflowNodeDetailItems(data: WorkflowNodeData): WorkflowNodeDetailItem[] {
  return [
    { label: "类型", value: nodeKindLabel(data.kind) },
    { label: "节点", value: data.id },
    { label: "描述", value: data.description },
    { label: "Agent", value: data.agentId || "" },
    { label: "人员", value: data.assignee },
    { label: "条件", value: data.conditionDescription },
    { label: "内容", value: data.conditionContent },
    { label: "交代", value: data.instruction },
    { label: "交代", value: data.handoffInstruction },
  ].map((item) => ({ ...item, value: String(item.value || "") })).filter((item) => item.value.trim())
}

export function workflowNodeInlineEditFields(data: WorkflowNodeData): WorkflowNodeInlineEditField[] {
  if (data.kind === "human") {
    return [
      {
        key: "assignee",
        label: "指定人员",
        inputType: "input",
        value: String(data.assignee || ""),
        placeholder: "人员姓名、角色或用户 ID",
      },
      {
        key: "handoff_instruction",
        label: "人工交代",
        inputType: "textarea",
        value: String(data.handoffInstruction || ""),
        placeholder: "给人工确认人的处理要求、注意事项或输出格式",
      },
    ]
  }
  if (data.kind === "condition") {
    return [
      {
        key: "condition_description",
        label: "条件描述",
        inputType: "input",
        value: String(data.conditionDescription || ""),
        placeholder: "例如：人工通过后完成，否则返工",
      },
      {
        key: "condition_content",
        label: "条件内容",
        inputType: "textarea",
        value: String(data.conditionContent || ""),
        placeholder: "例如：decision=approved -> 完成；decision=rejected -> 返工",
      },
    ]
  }
  return []
}

export function workflowToReactFlow(
  definition: WorkflowDefinition,
  positions: WorkflowNodePositions = defaultWorkflowNodePositions,
): { nodes: WorkflowReactFlowNode[]; edges: WorkflowReactFlowEdge[] } {
  const resolvedPositions = resolveNodePositions(definition, positions)
  return {
    nodes: definition.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode",
      position: nodePosition(node.id, resolvedPositions),
      style: {
        width: node.type === "condition" ? 180 : 224,
      },
      data: {
        id: node.id,
        title: node.title || node.id,
        description: node.description || "",
        kind: node.type,
        agentId: node.agent_id,
        instruction: String(node.config?.execution_instruction || ""),
        assignee: String(node.config?.assignee || ""),
        handoffInstruction: String(node.config?.handoff_instruction || ""),
        conditionDescription: String(node.config?.condition_description || ""),
        conditionContent: String(node.config?.condition_content || ""),
      },
    })),
    edges: definition.edges.map((edge) => workflowEdgeToReactFlow(edge)),
  }
}

export function reactFlowToWorkflow(nodes: WorkflowNode[], edges: WorkflowReactFlowEdge[]): WorkflowDefinition {
  return {
    nodes,
    edges: edges.map((edge) => ({
      from: edge.source,
      to: edge.target,
      condition: edge.data?.condition && typeof edge.data.condition === "object" ? edge.data.condition as Record<string, unknown> : {},
    })),
  }
}

export function applyNodeInstruction(definition: WorkflowDefinition, nodeId: string, instruction: string): WorkflowDefinition {
  return applyNodeConfig(definition, nodeId, { execution_instruction: instruction }, "agent")
}

export function applyNodeConfig(
  definition: WorkflowDefinition,
  nodeId: string,
  patch: Record<string, unknown>,
  expectedType?: string,
): WorkflowDefinition {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => {
      if (node.id !== nodeId) return node
      if (expectedType && node.type !== expectedType) return node
      return {
        ...node,
        config: {
          ...(node.config || {}),
          ...patch,
        },
      }
    }),
  }
}

function nodePosition(nodeId: string, positions: WorkflowNodePositions): XYPosition {
  const position = positions[nodeId] || defaultWorkflowNodePositions[nodeId] || { left: 0, top: 0 }
  return { x: position.left, y: position.top }
}

function resolveNodePositions(definition: WorkflowDefinition, positions: WorkflowNodePositions): WorkflowNodePositions {
  const resolved: WorkflowNodePositions = { ...positions }
  const nodesWithoutPosition = definition.nodes.filter((node) => !resolved[node.id] && !defaultWorkflowNodePositions[node.id])
  if (!nodesWithoutPosition.length) return resolved

  const incoming = new Map<string, string[]>()
  const outgoing = new Map<string, string[]>()
  definition.nodes.forEach((node) => {
    incoming.set(node.id, [])
    outgoing.set(node.id, [])
  })
  definition.edges.forEach((edge) => {
    incoming.get(edge.to)?.push(edge.from)
    outgoing.get(edge.from)?.push(edge.to)
  })

  const depth = new Map<string, number>()
  const queue = definition.nodes.filter((node) => node.id === "start" || !incoming.get(node.id)?.length).map((node) => node.id)
  queue.forEach((nodeId) => depth.set(nodeId, nodeId === "start" ? 0 : 1))
  while (queue.length) {
    const nodeId = queue.shift()!
    const nextDepth = (depth.get(nodeId) || 0) + 1
    for (const target of outgoing.get(nodeId) || []) {
      if (depth.has(target)) continue
      depth.set(target, nextDepth)
      queue.push(target)
    }
  }

  const grouped = new Map<number, WorkflowNode[]>()
  definition.nodes.forEach((node, index) => {
    if (resolved[node.id] || defaultWorkflowNodePositions[node.id]) return
    const nodeDepth = depth.get(node.id) ?? index + 1
    const group = grouped.get(nodeDepth) || []
    group.push(node)
    grouped.set(nodeDepth, group)
  })

  grouped.forEach((nodes, nodeDepth) => {
    nodes.forEach((node, index) => {
      resolved[node.id] = {
        left: 46 + nodeDepth * 230,
        top: 90 + index * 150,
      }
    })
  })
  return resolved
}

function workflowEdgeToReactFlow(edge: WorkflowEdge): WorkflowReactFlowEdge {
  return {
    id: `${edge.from}-${edge.to}`,
    source: edge.from,
    target: edge.to,
    type: "smoothstep",
    markerEnd: { type: "arrowclosed" },
    data: { condition: edge.condition || {} },
    animated: Boolean(edge.condition && Object.keys(edge.condition).length > 0),
  }
}

function nodeKindLabel(kind: string): string {
  return { start: "开始", agent: "Agent", human: "人工", condition: "条件", end: "完成" }[kind] || kind
}
