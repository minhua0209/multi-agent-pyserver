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
}

export type WorkflowReactFlowNode = Node<WorkflowNodeData, "workflowNode">
export type WorkflowReactFlowEdge = Edge<Record<string, unknown>>

export function workflowToReactFlow(
  definition: WorkflowDefinition,
  positions: WorkflowNodePositions = defaultWorkflowNodePositions,
): { nodes: WorkflowReactFlowNode[]; edges: WorkflowReactFlowEdge[] } {
  return {
    nodes: definition.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode",
      position: nodePosition(node.id, positions),
      data: {
        id: node.id,
        title: node.title || node.id,
        description: node.description || "",
        kind: node.type,
        agentId: node.agent_id,
        instruction: String(node.config?.execution_instruction || ""),
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
  return {
    ...definition,
    nodes: definition.nodes.map((node) => {
      if (node.id !== nodeId || node.type !== "agent") return node
      return {
        ...node,
        config: {
          ...(node.config || {}),
          execution_instruction: instruction,
        },
      }
    }),
  }
}

function nodePosition(nodeId: string, positions: WorkflowNodePositions): XYPosition {
  const position = positions[nodeId] || defaultWorkflowNodePositions[nodeId] || { left: 0, top: 0 }
  return { x: position.left, y: position.top }
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
