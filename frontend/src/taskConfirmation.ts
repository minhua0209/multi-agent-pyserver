import type {
  DeliverableFormat,
  DeliverableKind,
  Task,
  TaskConfirmPayload,
} from "./api/taskhub"


export interface ConfirmationDraft {
  title: string
  description: string
  goal: string
  deliverableGoal: string
  deliverableKind: DeliverableKind
  deliverableFormat: DeliverableFormat | null
  deliverableFilename: string
  deliverableRequirements: string[]
  successCriteria: string[]
  requiresHumanAcceptance: boolean
}

interface DraftSuggestions {
  title?: string
  description?: string
  goal?: string
  deliverable_goal?: string
  deliverable_kind?: unknown
  deliverable_format?: unknown
  deliverable_filename?: unknown
  deliverable_requirements?: string[]
  success_criteria?: string[]
  requires_human_acceptance?: boolean
}

interface ConfirmationContract {
  goal: string
  deliverable_goal: string
  deliverable_kind: DeliverableKind
  deliverable_format: DeliverableFormat | null
  deliverable_filename: string
  deliverable_requirements: Array<{ id: string; description: string }>
  success_criteria: Array<{ id: string; description: string }>
  requires_human_acceptance: boolean
}

export type ConfirmOptions = Omit<TaskConfirmPayload, "title" | "description" | "contract">

export interface TaskConfirmationRequest {
  taskId: string
  payload: TaskConfirmPayload & { contract: ConfirmationContract }
}

export const MAX_ACCEPTANCE_CRITERIA = 10


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
  const deliverableKind: DeliverableKind = suggestions.deliverable_kind === "file"
    ? "file"
    : "text"
  const suggestedFormat = deliverableFormat(suggestions.deliverable_format)
  const deliverableFormatValue = deliverableKind === "file"
    ? suggestedFormat || "markdown"
    : null
  const deliverableFilename = deliverableKind === "file"
    && typeof suggestions.deliverable_filename === "string"
    ? cleanText(suggestions.deliverable_filename)
    : ""
  const suggestedSuccessCriteria = uniqueEntries([
    ...(suggestions.deliverable_requirements || []),
    ...(suggestions.success_criteria || []),
  ]).slice(0, MAX_ACCEPTANCE_CRITERIA)
  const successCriteria = suggestedSuccessCriteria.length
    ? suggestedSuccessCriteria
    : [`交付结果满足：${requestText || goal}`]

  return {
    title,
    description,
    goal,
    deliverableGoal,
    deliverableKind,
    deliverableFormat: deliverableFormatValue,
    deliverableFilename,
    deliverableRequirements: [],
    successCriteria,
    requiresHumanAcceptance: Boolean(suggestions.requires_human_acceptance),
  }
}

export function validateConfirmationDraft(draft: ConfirmationDraft): string[] {
  const errors: string[] = []
  if (!cleanText(draft.goal)) errors.push("请填写任务目标")
  if (!cleanText(draft.deliverableGoal)) errors.push("请填写交付物目标")
  if (!cleanEntries(draft.successCriteria).length) {
    errors.push("请至少填写一条验收标准")
  }
  if (cleanEntries(draft.successCriteria).length > MAX_ACCEPTANCE_CRITERIA) {
    errors.push(`验收标准最多填写 ${MAX_ACCEPTANCE_CRITERIA} 条`)
  }
  if (draft.deliverableKind === "file") {
    const format = deliverableFormat(draft.deliverableFormat)
    const filename = cleanText(draft.deliverableFilename)
    if (!format) errors.push("请选择文件格式")
    if (filename && isFilenamePath(filename)) {
      errors.push("文件名不能包含路径")
    } else if (filename && !isPlainFilename(filename)) {
      errors.push("文件名不能包含路径或 Windows 非法字符")
    } else if (filename && format) {
      const expectedExtension = format === "markdown" ? ".md" : ".txt"
      const extension = filenameExtension(filename)
      if (extension && extension !== expectedExtension) {
        errors.push(`文件扩展名与所选格式不匹配，应使用 ${expectedExtension}`)
      } else {
        const resolvedFilename = extension ? filename : `${filename}${expectedExtension}`
        if (new TextEncoder().encode(resolvedFilename).length > 255) {
          errors.push("文件名不能超过 255 个 UTF-8 字节")
        }
      }
    }
  }
  return errors
}

export function setConfirmationDeliverableKind(
  draft: ConfirmationDraft,
  kind: DeliverableKind,
): ConfirmationDraft {
  if (kind === "text") {
    return {
      ...draft,
      deliverableKind: "text",
      deliverableFormat: null,
      deliverableFilename: "",
    }
  }
  return {
    ...draft,
    deliverableKind: "file",
    deliverableFormat: deliverableFormat(draft.deliverableFormat) || "markdown",
  }
}

export function buildTaskConfirmPayload(
  draft: ConfirmationDraft,
  options: ConfirmOptions = {},
): TaskConfirmPayload & { contract: ConfirmationContract } {
  const isFile = draft.deliverableKind === "file"
  return {
    title: cleanText(draft.title),
    description: cleanText(draft.description),
    ...options,
    contract: {
      goal: cleanText(draft.goal),
      deliverable_goal: cleanText(draft.deliverableGoal),
      deliverable_kind: isFile ? "file" : "text",
      deliverable_format: isFile ? deliverableFormat(draft.deliverableFormat) : null,
      deliverable_filename: isFile ? cleanText(draft.deliverableFilename) : "",
      deliverable_requirements: [],
      success_criteria: uniqueEntries([
        ...draft.deliverableRequirements,
        ...draft.successCriteria,
      ]).slice(0, MAX_ACCEPTANCE_CRITERIA).map((description) => ({
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

function uniqueEntries(values: string[]) {
  return [...new Set(cleanEntries(values))]
}

function cleanText(value: string) {
  return String(value || "").trim()
}

function deliverableFormat(value: unknown): DeliverableFormat | null {
  return value === "markdown" || value === "text" ? value : null
}

function isPlainFilename(filename: string) {
  if (
    filename.endsWith(".")
    || /[<>:"/\\|?*]/.test(filename)
    || /[\u0000-\u001f\u007f-\u009f]/.test(filename)
  ) {
    return false
  }
  const deviceBasename = filename.split(".", 1)[0].toUpperCase()
  return !(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"].includes(deviceBasename)
    || /^(COM|LPT)[123456789¹²³]$/.test(deviceBasename)
  )
}

function isFilenamePath(filename: string) {
  return filename === "." || filename === ".." || /[/\\]/.test(filename)
}

function filenameExtension(filename: string) {
  const dotIndex = filename.lastIndexOf(".")
  return dotIndex > 0 ? filename.slice(dotIndex).toLowerCase() : ""
}
