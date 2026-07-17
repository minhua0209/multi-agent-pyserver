import type { SubTask } from "./api/taskhub"

export function humanReviewDocumentText(subtask?: Pick<SubTask, "upstream_outputs" | "task_context_summary"> | null) {
  if (!subtask) return ""
  const upstreamText = (subtask.upstream_outputs || []).filter(Boolean).join("\n\n")
  return upstreamText || subtask.task_context_summary || ""
}

export function humanReviewDocumentSourceLabel(subtask?: Pick<SubTask, "upstream_outputs" | "task_context_summary"> | null) {
  if (!subtask) return "暂无文档"
  if ((subtask.upstream_outputs || []).filter(Boolean).length) return "上游产出"
  if (subtask.task_context_summary) return "上下文"
  return "暂无文档"
}
