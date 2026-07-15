import { WorkflowEdge, WorkflowNode } from "./api/taskhub"

export interface CanvasSize {
  width: number
  height: number
}

export interface NodeSize {
  width: number
  height: number
}

export interface NodePosition {
  left: number
  top: number
}

export type WorkflowNodePositions = Record<string, NodePosition>

export interface WorkflowEdgePath {
  id: string
  className: string
  markerId: "workflow-arrow" | "workflow-arrow-condition"
  path: string
}

export const workflowCanvasSize: CanvasSize = { width: 2400, height: 1600 }

export const workflowNodeSize: NodeSize = { width: 160, height: 104 }

export const workflowConditionNodeSize: NodeSize = { width: 136, height: 104 }

export const defaultWorkflowNodePositions: WorkflowNodePositions = {
  start: { left: 46, top: 268 },
  parallel_agent_1: { left: 236, top: 82 },
  parallel_agent_2: { left: 236, top: 242 },
  parallel_agent_3: { left: 236, top: 402 },
  review: { left: 520, top: 242 },
  judge: { left: 682, top: 238 },
  end: { left: 800, top: 92 },
  revise: { left: 800, top: 392 },
}

interface EdgeSpec {
  source: string
  target: string
  className: string
  markerId: WorkflowEdgePath["markerId"]
  loop?: boolean
}

export const defaultWorkflowEdges: WorkflowEdge[] = [
  { from: "start", to: "parallel_agent_1", condition: {} },
  { from: "start", to: "parallel_agent_2", condition: {} },
  { from: "start", to: "parallel_agent_3", condition: {} },
  { from: "parallel_agent_1", to: "review", condition: {} },
  { from: "parallel_agent_2", to: "review", condition: {} },
  { from: "parallel_agent_3", to: "review", condition: {} },
  { from: "review", to: "judge", condition: {} },
  { from: "judge", to: "end", condition: { type: "decision", value: "approved" } },
  { from: "judge", to: "revise", condition: { type: "decision", value: "rejected" } },
  { from: "revise", to: "review", condition: {} },
]

export function nodeSize(nodeId: string): NodeSize {
  return nodeId === "judge" || nodeId.startsWith("condition") ? workflowConditionNodeSize : workflowNodeSize
}

export function clampNodePosition(position: NodePosition, size: NodeSize, canvas: CanvasSize): NodePosition {
  return {
    left: Math.max(0, Math.min(position.left, canvas.width - size.width)),
    top: Math.max(0, Math.min(position.top, canvas.height - size.height)),
  }
}

export function expandCanvasSizeForNode(canvas: CanvasSize, position: NodePosition, size: NodeSize, padding = 360): CanvasSize {
  return {
    width: Math.max(canvas.width, position.left + size.width + padding),
    height: Math.max(canvas.height, position.top + size.height + padding),
  }
}

function rightAnchor(nodeId: string, positions: WorkflowNodePositions) {
  const position = positions[nodeId]
  const size = nodeSize(nodeId)
  return { x: position.left + size.width, y: position.top + size.height / 2 }
}

function leftAnchor(nodeId: string, positions: WorkflowNodePositions) {
  const position = positions[nodeId]
  const size = nodeSize(nodeId)
  return { x: position.left, y: position.top + size.height / 2 }
}

function edgePath(sourceId: string, targetId: string, positions: WorkflowNodePositions, canvas: CanvasSize, loop = false) {
  const source = rightAnchor(sourceId, positions)
  const target = leftAnchor(targetId, positions)
  if (loop || target.x < source.x) {
    const controlY = Math.min(canvas.height - 28, Math.max(source.y, target.y) + 96)
    return `M${source.x} ${source.y} C${source.x - 90} ${controlY}, ${target.x + 90} ${controlY}, ${target.x} ${target.y}`
  }
  const middleX = source.x + (target.x - source.x) / 2
  return `M${source.x} ${source.y} C${middleX} ${source.y}, ${middleX} ${target.y}, ${target.x} ${target.y}`
}

export function buildWorkflowEdgePathsFromSpecs(
  positions: WorkflowNodePositions,
  specs: EdgeSpec[],
  canvas: CanvasSize = workflowCanvasSize,
): WorkflowEdgePath[] {
  return specs.filter((edge) => positions[edge.source] && positions[edge.target]).map((edge) => ({
    id: `${edge.source}-${edge.target}`,
    className: edge.className,
    markerId: edge.markerId,
    path: edgePath(edge.source, edge.target, positions, canvas, edge.loop),
  }))
}

export function workflowEdgeToSpec(edge: WorkflowEdge): EdgeSpec {
  const decisionValue = edge.condition?.value
  if (edge.condition?.type === "decision" && decisionValue === "rejected") {
    return {
      source: edge.from,
      target: edge.to,
      className: "workflow-edge rejected",
      markerId: "workflow-arrow-condition",
      loop: edge.to === "review" || edge.from === "revise",
    }
  }
  if (edge.condition?.type === "decision") {
    return {
      source: edge.from,
      target: edge.to,
      className: "workflow-edge condition",
      markerId: "workflow-arrow-condition",
    }
  }
  return {
    source: edge.from,
    target: edge.to,
    className: "workflow-edge",
    markerId: "workflow-arrow",
  }
}

export function buildWorkflowEdgePaths(
  positions: WorkflowNodePositions,
  edges: WorkflowEdge[] = defaultWorkflowEdges,
  canvas: CanvasSize = workflowCanvasSize,
): WorkflowEdgePath[] {
  return buildWorkflowEdgePathsFromSpecs(positions, edges.map(workflowEdgeToSpec), canvas)
}

export function removeWorkflowNode(nodes: WorkflowNode[], edges: WorkflowEdge[], nodeId: string) {
  if (nodeId === "start" || nodeId === "end") {
    return { nodes, edges }
  }
  return {
    nodes: nodes.filter((node) => node.id !== nodeId),
    edges: edges.filter((edge) => edge.from !== nodeId && edge.to !== nodeId),
  }
}
