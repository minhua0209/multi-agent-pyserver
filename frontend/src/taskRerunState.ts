import type { TaskRerunCreate } from "./api/taskhub"


export interface PendingTaskRerunRequest {
  readonly idempotencyKey: string
  readonly payload: Readonly<TaskRerunCreate>
}

interface TaskRerunSubmitState {
  pendingRequest: PendingTaskRerunRequest | null
  preflightAllowed: boolean
  reason: string
  requiresSideEffectConfirmation: boolean
  confirmSideEffects: boolean
}

const storageKeyPrefix = "taskhub_pending_rerun:"


export function loadPendingTaskRerun(
  taskId: string,
  storage: Storage | null = browserSessionStorage(),
): PendingTaskRerunRequest | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(storageKey(taskId))
    if (!raw) return null
    return parsePendingRequest(JSON.parse(raw))
  } catch {
    return null
  }
}

export function ensurePendingTaskRerun(
  taskId: string,
  payload: TaskRerunCreate,
  storage: Storage | null = browserSessionStorage(),
  createIdempotencyKey: () => string = () => defaultIdempotencyKey(taskId),
): PendingTaskRerunRequest {
  const existing = loadPendingTaskRerun(taskId, storage)
  if (existing) return existing
  if (!storage) throw new Error("当前浏览器无法保存待确认的重跑请求")

  const pending = parsePendingRequest({
    idempotencyKey: createIdempotencyKey(),
    payload: JSON.parse(JSON.stringify(payload)),
  })
  if (!pending) throw new Error("无法创建待确认的重跑请求")
  storage.setItem(storageKey(taskId), JSON.stringify(pending))
  return pending
}

export function clearPendingTaskRerun(
  taskId: string,
  storage: Storage | null = browserSessionStorage(),
) {
  if (!storage) return
  try {
    storage.removeItem(storageKey(taskId))
  } catch {
    // Clearing best-effort browser state must not hide a successful API response.
  }
}

export function canSubmitTaskRerun(state: TaskRerunSubmitState) {
  if (state.pendingRequest) return true
  if (!state.preflightAllowed || !state.reason.trim()) return false
  return !state.requiresSideEffectConfirmation || state.confirmSideEffects
}

export function shouldBlockTaskRerunFormForPreflight(
  loading: boolean,
  pendingRequest: PendingTaskRerunRequest | null,
) {
  return loading && !pendingRequest
}

function storageKey(taskId: string) {
  return `${storageKeyPrefix}${taskId}`
}

function browserSessionStorage() {
  try {
    if (typeof globalThis.sessionStorage === "undefined") return null
    return globalThis.sessionStorage
  } catch {
    return null
  }
}

function defaultIdempotencyKey(taskId: string) {
  const randomId = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `task-rerun-${taskId}-${randomId}`
}

function parsePendingRequest(value: unknown): PendingTaskRerunRequest | null {
  if (!isRecord(value) || typeof value.idempotencyKey !== "string" || !value.idempotencyKey.trim()) {
    return null
  }
  const payload = value.payload
  if (
    !isRecord(payload)
    || typeof payload.source_execution_id !== "string"
    || !payload.source_execution_id.trim()
    || typeof payload.reason !== "string"
    || !payload.reason.trim()
    || (payload.execution_mode !== undefined && payload.execution_mode !== "sync" && payload.execution_mode !== "async")
    || (payload.confirm_side_effects !== undefined && typeof payload.confirm_side_effects !== "boolean")
  ) {
    return null
  }
  const frozenPayload = Object.freeze({
    source_execution_id: payload.source_execution_id,
    reason: payload.reason,
    ...(payload.execution_mode ? { execution_mode: payload.execution_mode } : {}),
    ...(payload.confirm_side_effects !== undefined
      ? { confirm_side_effects: payload.confirm_side_effects }
      : {}),
  })
  return Object.freeze({
    idempotencyKey: value.idempotencyKey,
    payload: frozenPayload,
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
