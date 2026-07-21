import type { Task, TaskConfirmPayload } from "./api/taskhub"


export interface ConfirmationDraft {
  title: string
  description: string
  goal: string
  deliverableGoal: string
  deliverableRequirements: string[]
  successCriteria: string[]
  requiresHumanAcceptance: boolean
}

interface DraftSuggestions {
  title?: string
  description?: string
  goal?: string
  deliverable_goal?: string
  deliverable_requirements?: string[]
  success_criteria?: string[]
  requires_human_acceptance?: boolean
}

interface ConfirmationContract {
  goal: string
  deliverable_goal: string
  deliverable_requirements: Array<{ id: string; description: string }>
  success_criteria: Array<{ id: string; description: string }>
  requires_human_acceptance: boolean
}

export type ConfirmOptions = Omit<TaskConfirmPayload, "title" | "description" | "contract">

export interface TaskConfirmationRequest {
  taskId: string
  payload: TaskConfirmPayload & { contract: ConfirmationContract }
}


export function confirmationDraftFromTask(task: Task): ConfirmationDraft {
  const suggestions = (task.draft || {}) as DraftSuggestions
  const title = cleanText(task.title || suggestions.title || task.content || task.id)
  const description = cleanText(
    suggestions.description || task.description || task.content || title,
  )
  const requestText = cleanText(task.description || task.content || description || title)
  const goal = cleanText(suggestions.goal || requestText || title)
  const deliverableGoal = cleanText(
    suggestions.deliverable_goal || `形成可评审的${title}交付物`,
  )
  const deliverableRequirements = cleanEntries(
    suggestions.deliverable_requirements || [],
  )
  const suggestedSuccessCriteria = cleanEntries(suggestions.success_criteria || [])
  const successCriteria = suggestedSuccessCriteria.length
    ? suggestedSuccessCriteria
    : [`交付结果满足：${requestText || goal}`]

  return {
    title,
    description,
    goal,
    deliverableGoal,
    deliverableRequirements,
    successCriteria,
    requiresHumanAcceptance: Boolean(suggestions.requires_human_acceptance),
  }
}

export function validateConfirmationDraft(draft: ConfirmationDraft): string[] {
  const errors: string[] = []
  if (!cleanText(draft.goal)) errors.push("请填写任务目标")
  if (!cleanText(draft.deliverableGoal)) errors.push("请填写交付物目标")
  if (!cleanEntries(draft.successCriteria).length) {
    errors.push("请至少填写一条成功标准")
  }
  return errors
}

export function buildTaskConfirmPayload(
  draft: ConfirmationDraft,
  options: ConfirmOptions = {},
): TaskConfirmPayload & { contract: ConfirmationContract } {
  return {
    title: cleanText(draft.title),
    description: cleanText(draft.description),
    ...options,
    contract: {
      goal: cleanText(draft.goal),
      deliverable_goal: cleanText(draft.deliverableGoal),
      deliverable_requirements: cleanEntries(draft.deliverableRequirements).map(
        (description) => ({ id: "", description }),
      ),
      success_criteria: cleanEntries(draft.successCriteria).map((description) => ({
        id: "",
        description,
      })),
      requires_human_acceptance: draft.requiresHumanAcceptance,
    },
  }
}

export function isTaskAwaitingConfirmation(task: Task): boolean {
  const status = task.task_status || task.status || "running"
  return status === "running" && task.current_node === "human_confirmation"
}

export function buildTaskConfirmationRequests(
  tasks: Task[],
  drafts: Record<string, ConfirmationDraft>,
  options: ConfirmOptions = {},
): TaskConfirmationRequest[] {
  return tasks.map((task) => ({
    taskId: task.id,
    payload: buildTaskConfirmPayload(
      drafts[task.id] || confirmationDraftFromTask(task),
      options,
    ),
  }))
}

export function confirmationTaskIdsToCancelOnClose(
  tasks: Task[],
  remainingTaskIds: string[],
  cancelOnClose = true,
): string[] {
  if (!cancelOnClose) return []
  const remaining = new Set(remainingTaskIds)
  return tasks.filter((task) => remaining.has(task.id)).map((task) => task.id)
}

export async function confirmTaskRequestsSequentially(
  requests: TaskConfirmationRequest[],
  confirm: (taskId: string, payload: TaskConfirmPayload) => Promise<Task>,
  reconcile: (taskId: string) => Promise<Task>,
  onConfirmed: (task: Task) => void | Promise<void>,
): Promise<void> {
  for (const request of requests) {
    let confirmed: Task
    try {
      confirmed = await confirm(request.taskId, request.payload)
    } catch (error) {
      let latest: Task | null = null
      try {
        latest = await reconcile(request.taskId)
      } catch {
        // Keep the original confirmation error when reconciliation is unavailable.
      }
      if (!latest?.contract || isTaskAwaitingConfirmation(latest)) throw error
      confirmed = latest
    }
    await onConfirmed(confirmed)
  }
}

export async function cancelTasksSequentially(
  taskIds: string[],
  cancel: (taskId: string) => Promise<void>,
  reconcile: (taskId: string) => Promise<Task>,
  onCancelled: (taskId: string) => void | Promise<void>,
): Promise<void> {
  for (const taskId of taskIds) {
    try {
      await cancel(taskId)
    } catch (error) {
      let latest: Task | null = null
      try {
        latest = await reconcile(taskId)
      } catch {
        // Keep the original cancellation error when reconciliation is unavailable.
      }
      const status = latest?.task_status || latest?.status
      if (status !== "cancelled") throw error
    }
    await onCancelled(taskId)
  }
}

function cleanEntries(values: string[]) {
  return values.map(cleanText).filter(Boolean)
}

function cleanText(value: string) {
  return String(value || "").trim()
}
