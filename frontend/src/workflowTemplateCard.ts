import { WorkflowTemplate } from "./api/taskhub"

const STATUS_LABELS: Record<string, string> = {
  active: "启用",
  disabled: "停用",
  draft: "草稿",
}

export function workflowTemplateCardView(workflow: WorkflowTemplate) {
  const nodeCount = workflow.definition.nodes.length
  const edgeCount = workflow.definition.edges.length
  return {
    title: workflow.name || "未命名流程",
    description: workflow.description || "暂无描述",
    statusLabel: STATUS_LABELS[workflow.status || "active"] || workflow.status || "启用",
    nodeCountLabel: `${nodeCount} 个节点`,
    edgeCountLabel: `${edgeCount} 条连线`,
  }
}
