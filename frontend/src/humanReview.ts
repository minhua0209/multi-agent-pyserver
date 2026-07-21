import type { SubTask } from "./api/taskhub"

type HumanReviewDocumentSource = Pick<SubTask, "description" | "upstream_outputs" | "task_context_summary">

export function humanReviewDocumentText(subtask?: HumanReviewDocumentSource | null) {
  if (!subtask) return ""
  const instruction = String(subtask.description || "").trim()
  if (instruction) return instruction
  const upstreamText = (subtask.upstream_outputs || []).filter(Boolean).join("\n\n")
  return upstreamText || subtask.task_context_summary || ""
}

export function humanReviewDocumentSourceLabel(subtask?: HumanReviewDocumentSource | null) {
  if (!subtask) return "暂无文档"
  if (String(subtask.description || "").trim()) return "人工节点配置"
  if ((subtask.upstream_outputs || []).filter(Boolean).length) return "上游产出"
  if (subtask.task_context_summary) return "上下文"
  return "暂无文档"
}
