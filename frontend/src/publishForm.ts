export function initialPublishForm() {
  return {
    title: "",
    content: "",
    workflowId: "",
  }
}

export function validatePublishForm(title: string, content: string) {
  const trimmedTitle = title.trim()
  if (!trimmedTitle) return "请填写任务名称"
  if (trimmedTitle.length > 50) return "任务名称不能超过 50 个字"
  if (!content.trim()) return "请填写任务诉求"
  return ""
}

export function validateWorkflowBuilderOpen(_title: string, _content: string) {
  return ""
}
