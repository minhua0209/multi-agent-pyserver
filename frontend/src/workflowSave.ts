import type { WorkflowCreatePayload, WorkflowTemplate } from "./api/taskhub"

export const workflowBuilderCopy = {
  title: "流程节点编排",
  description: "选择流程节点，在自由画布中配置执行节点、人工确认、条件判断和上下文流转。",
  saveButton: "保存流程模板",
  savingButton: "保存中",
  createdToast: "流程模板已保存，可在任务发布时选择",
  updatedToast: "流程模板已更新，已覆盖同名旧模板",
}

type WorkflowSaveAction =
  | {
      type: "create"
      payload: WorkflowCreatePayload
    }
  | {
      type: "update"
      workflowId: string
      payload: WorkflowCreatePayload
    }

export function workflowTemplateSaveAction(
  workflows: WorkflowTemplate[],
  payload: WorkflowCreatePayload,
): WorkflowSaveAction {
  const normalizedName = payload.name.trim()
  const normalizedPayload = {
    ...payload,
    name: normalizedName,
  }
  const existing = workflows.find((workflow) => workflow.name.trim() === normalizedName)
  if (!existing) {
    return {
      type: "create",
      payload: normalizedPayload,
    }
  }
  return {
    type: "update",
    workflowId: existing.id,
    payload: normalizedPayload,
  }
}
