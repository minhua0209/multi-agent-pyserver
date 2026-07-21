import { Edge, Node, XYPosition } from "@xyflow/react"

import { WorkflowDefinition, WorkflowEdge, WorkflowNode } from "./api/taskhub"
import { WorkflowNodePositions } from "./workflowCanvas"

export interface WorkflowNodeData extends Record<string, unknown> {
  id: string
  title: string
  description: string
  kind: string
  agentId?: string | null
  agentName?: string | null
  instruction: string
  assignee?: string
  assigneeUserId?: string
  assigneeUserName?: string
  assigneeRole?: string
  userOptions?: Array<{ id: string; name: string; role: string }>
  handoffInstruction?: string
  conditionDescription?: string
  conditionContent?: string
  conditionOptions?: WorkflowConditionOption[]
  allowedDecisions?: string[]
  editing?: boolean
  onConfigChange?: (nodeId: string, patch: Record<string, unknown>) => void
  onEditStart?: (nodeId: string) => void
  onEditEnd?: () => void
}

export interface WorkflowConditionOption {
  value: string
  content: string
}

export type WorkflowReactFlowNode = Node<WorkflowNodeData, "workflowNode">
export type WorkflowReactFlowEdge = Edge<Record<string, unknown>>

export interface WorkflowAutoLayoutOptions {
  left?: number
  top?: number
  columnGap?: number
  rowGap?: number
  columnsPerRow?: number
}

export interface WorkflowNodeDetailItem {
  label: string
  value: string
}

export interface WorkflowNodeInlineEditField {
  key: string
  label: string
  inputType: "input" | "textarea" | "user_select" | "condition_options"
  value: string
  placeholder: string
  conditionOptions?: WorkflowConditionOption[]
}

export interface WorkflowInlineTextDraftState {
  value: string
  composing: boolean
}

export type WorkflowInlineTextDraftAction =
  | { type: "external_value"; value: string }
  | { type: "composition_start" }
  | { type: "composition_end"; value: string }
  | { type: "change"; value: string; isComposing?: boolean }
  | { type: "blur"; value: string }

export function reduceWorkflowInlineTextDraft(
  state: WorkflowInlineTextDraftState,
  action: WorkflowInlineTextDraftAction,
): { state: WorkflowInlineTextDraftState; commitValue?: string } {
  if (action.type === "external_value") {
    if (state.composing) return { state }
    return { state: { ...state, value: action.value } }
  }
  if (action.type === "composition_start") {
    return { state: { ...state, composing: true } }
  }
  if (action.type === "composition_end") {
    return {
      state: { value: action.value, composing: false },
      commitValue: action.value,
    }
  }
  if (action.type === "change") {
    const composing = Boolean(action.isComposing || state.composing)
    return {
      state: { value: action.value, composing },
      commitValue: composing ? undefined : action.value,
    }
  }
  return {
    state: { value: action.value, composing: false },
    commitValue: action.value,
  }
}

export function workflowNodeDetailItems(data: WorkflowNodeData): WorkflowNodeDetailItem[] {
  return [
    { label: "类型", value: nodeKindLabel(data.kind) },
    { label: "节点", value: data.id },
    { label: "描述", value: data.description },
    { label: "Agent", value: data.agentName || data.agentId || "" },
    { label: "人员", value: data.assigneeUserName || data.assignee },
    { label: "角色", value: data.assigneeRole },
    { label: "条件", value: data.conditionDescription },
    { label: "条件项", value: formatWorkflowConditionOptions(data.conditionOptions) },
    { label: "内容", value: data.conditionContent },
    { label: "交代", value: data.instruction },
    { label: "交代", value: data.handoffInstruction },
  ].map((item) => ({ ...item, value: String(item.value || "") })).filter((item) => item.value.trim())
}

export function workflowNodeInlineEditFields(data: WorkflowNodeData): WorkflowNodeInlineEditField[] {
  if (data.kind === "human") {
    return [
      {
        key: "assignee_user_id",
        label: "指定人员",
        inputType: "user_select",
        value: String(data.assigneeUserId || ""),
        placeholder: "请选择人员姓名",
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
        key: "condition_options",
        label: "分支条件",
        inputType: "condition_options",
        value: JSON.stringify(workflowConditionOptionsForData(data)),
        placeholder: "配置边条件值和对应判断说明",
        conditionOptions: workflowConditionOptionsForData(data),
      },
    ]
  }
  return []
}

export function workflowToReactFlow(
  definition: WorkflowDefinition,
  positions?: WorkflowNodePositions,
): { nodes: WorkflowReactFlowNode[]; edges: WorkflowReactFlowEdge[] } {
  const resolvedPositions = resolveNodePositions(definition, positions)
  return {
    nodes: definition.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode",
      position: nodePosition(node.id, resolvedPositions),
      style: {
        width: node.type === "condition" ? 220 : 260,
      },
      data: {
        id: node.id,
        title: node.title || node.id,
        description: node.description || "",
        kind: node.type,
        agentId: node.agent_id,
        agentName: String(node.config?.agent_name || node.config?.agentName || ""),
        instruction: String(node.config?.execution_instruction || ""),
        assignee: String(node.config?.assignee_user_name || node.config?.assignee || ""),
        assigneeUserId: String(node.config?.assignee_user_id || ""),
        assigneeUserName: String(node.config?.assignee_user_name || node.config?.assignee || ""),
        assigneeRole: String(node.config?.assignee_role || ""),
        handoffInstruction: String(node.config?.handoff_instruction || ""),
        conditionDescription: String(node.config?.condition_description || ""),
        conditionContent: String(node.config?.condition_content || ""),
        conditionOptions: normalizeWorkflowConditionOptions(node.config?.condition_options),
        allowedDecisions: stringList(node.config?.allowed_decisions),
      },
    })),
    edges: definition.edges.map((edge) => workflowEdgeToReactFlow(edge)),
  }
}

export function autoLayoutWorkflowNodePositions(
  definition: WorkflowDefinition,
  options: WorkflowAutoLayoutOptions = {},
): WorkflowNodePositions {
  const left = options.left ?? 80
  const top = options.top ?? 90
  const columnGap = options.columnGap ?? 340
  const rowGap = options.rowGap ?? 220
  const columnsPerRow = options.columnsPerRow ?? 5
  const nodes = definition.nodes || []
  if (!nodes.length) return {}

  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const nodeOrder = new Map(nodes.map((node, index) => [node.id, index]))
  const outgoing = new Map<string, string[]>()
  const incomingCount = new Map<string, number>()
  nodes.forEach((node) => {
    outgoing.set(node.id, [])
    incomingCount.set(node.id, 0)
  })
  definition.edges.forEach((edge) => {
    if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) return
    outgoing.get(edge.from)?.push(edge.to)
    incomingCount.set(edge.to, (incomingCount.get(edge.to) || 0) + 1)
  })
  outgoing.forEach((targets) => targets.sort((first, second) => (nodeOrder.get(first) || 0) - (nodeOrder.get(second) || 0)))

  const depths = new Map<string, number>()
  const rootIds = nodes
    .filter((node) => node.id === "start" || (incomingCount.get(node.id) || 0) === 0)
    .map((node) => node.id)
  const roots = rootIds.length ? rootIds : nodes.map((node) => node.id)

  function visit(nodeId: string, depth: number, visiting: Set<string>) {
    const previousDepth = depths.get(nodeId)
    if (previousDepth !== undefined && previousDepth >= depth) return
    depths.set(nodeId, depth)

    const nextVisiting = new Set(visiting)
    nextVisiting.add(nodeId)
    for (const target of outgoing.get(nodeId) || []) {
      if (nextVisiting.has(target)) continue
      visit(target, depth + 1, nextVisiting)
    }
  }

  roots.forEach((nodeId) => visit(nodeId, 0, new Set<string>()))
  nodes.forEach((node) => {
    if (!depths.has(node.id)) visit(node.id, 0, new Set<string>())
  })

  const grouped = new Map<number, WorkflowNode[]>()
  nodes.forEach((node) => {
    const depth = depths.get(node.id) || 0
    const group = grouped.get(depth) || []
    group.push(node)
    grouped.set(depth, group)
  })
  grouped.forEach((group) => group.sort((first, second) => (nodeOrder.get(first.id) || 0) - (nodeOrder.get(second.id) || 0)))

  const maxRows = Math.max(1, ...Array.from(grouped.values()).map((group) => group.length))
  const bandHeight = maxRows * rowGap + 180
  const resolved: WorkflowNodePositions = {}
  Array.from(grouped.entries()).sort(([firstDepth], [secondDepth]) => firstDepth - secondDepth).forEach(([depth, group]) => {
    const visualColumn = depth % columnsPerRow
    const visualRow = Math.floor(depth / columnsPerRow)
    const groupOffset = ((maxRows - group.length) * rowGap) / 2
    group.forEach((node, index) => {
      resolved[node.id] = {
        left: left + visualColumn * columnGap,
        top: Math.round(top + visualRow * bandHeight + groupOffset + index * rowGap),
      }
    })
  })
  return resolved
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

export function canEditDecisionEdge(nodes: WorkflowNode[], edge: Pick<WorkflowReactFlowEdge, "source">): boolean {
  return nodes.some((node) => node.id === edge.source && node.type === "condition")
}

export function setDecisionEdgeCondition(edge: WorkflowReactFlowEdge, decision: string): WorkflowReactFlowEdge {
  const condition = decision ? { type: "decision", value: decision } : {}
  return {
    ...edge,
    data: {
      ...(edge.data || {}),
      condition,
    },
    label: decision || undefined,
    animated: Boolean(decision),
  }
}

export function workflowConditionDecisionValues(node: WorkflowNode | null | undefined): string[] {
  const values = normalizeWorkflowConditionOptions(node?.config?.condition_options)
    .map((option) => option.value)
    .filter((value) => value.trim())
  if (values.length) return uniqueStrings(values)
  return stringList(node?.config?.allowed_decisions)
}

export function normalizeWorkflowConditionOptions(value: unknown): WorkflowConditionOption[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null
      const data = item as Record<string, unknown>
      return {
        value: String(data.value || "").trim(),
        content: String(data.content || "").trim(),
      }
    })
    .filter((item): item is WorkflowConditionOption => Boolean(item))
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
  const position = positions[nodeId] || { left: 0, top: 0 }
  return { x: position.left, y: position.top }
}

function workflowConditionOptionsForData(data: WorkflowNodeData): WorkflowConditionOption[] {
  const configured = normalizeWorkflowConditionOptions(data.conditionOptions)
  if (configured.length) return configured
  const decisions = stringList(data.allowedDecisions)
  if (decisions.length) return decisions.map((decision) => ({ value: decision, content: "" }))
  return []
}

function formatWorkflowConditionOptions(options: unknown): string {
  return normalizeWorkflowConditionOptions(options)
    .filter((option) => option.value || option.content)
    .map((option) => (option.content ? `${option.value || "未命名"}：${option.content}` : option.value))
    .join("；")
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return uniqueStrings(value.map((item) => String(item || "").trim()).filter(Boolean))
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values))
}

function resolveNodePositions(definition: WorkflowDefinition, positions?: WorkflowNodePositions): WorkflowNodePositions {
  const resolved = autoLayoutWorkflowNodePositions(definition)
  return {
    ...resolved,
    ...(positions || {}),
  }
}

function workflowEdgeToReactFlow(edge: WorkflowEdge): WorkflowReactFlowEdge {
  const decision = edge.condition?.type === "decision" && typeof edge.condition.value === "string" ? edge.condition.value : ""
  return {
    id: `${edge.from}-${edge.to}`,
    source: edge.from,
    target: edge.to,
    type: "smoothstep",
    markerEnd: { type: "arrowclosed" },
    data: { condition: edge.condition || {} },
    label: decision || undefined,
    animated: Boolean(edge.condition && Object.keys(edge.condition).length > 0),
  }
}

function nodeKindLabel(kind: string): string {
  return { start: "开始", agent: "Agent", human: "人工", condition: "条件", end: "完成" }[kind] || kind
}
