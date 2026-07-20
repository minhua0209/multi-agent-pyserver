import { describe, expect, it } from "vitest"

import type { TaskRerunCreate } from "./api/taskhub"
import {
  canSubmitTaskRerun,
  clearPendingTaskRerun,
  ensurePendingTaskRerun,
  loadPendingTaskRerun,
  shouldBlockTaskRerunFormForPreflight,
} from "./taskRerunState"


class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  get length() {
    return this.values.size
  }

  clear() {
    this.values.clear()
  }

  getItem(key: string) {
    return this.values.get(key) ?? null
  }

  key(index: number) {
    return Array.from(this.values.keys())[index] ?? null
  }

  removeItem(key: string) {
    this.values.delete(key)
  }

  setItem(key: string, value: string) {
    this.values.set(key, value)
  }
}

function rerunPayload(): TaskRerunCreate {
  return {
    source_execution_id: "execution_1",
    reason: "补齐交付物",
    execution_mode: "async",
    confirm_side_effects: true,
  }
}


describe("pending task rerun state", () => {
  it("persists the first immutable request and never overwrites it implicitly", () => {
    const storage = new MemoryStorage()
    const submittedPayload = rerunPayload()
    const first = ensurePendingTaskRerun("task_1", submittedPayload, storage, () => "key_1")
    submittedPayload.reason = "调用后被修改"

    const second = ensurePendingTaskRerun(
      "task_1",
      {
        source_execution_id: "execution_2",
        reason: "新的重跑请求",
        execution_mode: "sync",
        confirm_side_effects: false,
      },
      storage,
      () => "key_2",
    )

    expect(first).toEqual({
      idempotencyKey: "key_1",
      payload: {
        source_execution_id: "execution_1",
        reason: "补齐交付物",
        execution_mode: "async",
        confirm_side_effects: true,
      },
    })
    expect(second).toEqual(first)
    expect(loadPendingTaskRerun("task_1", storage)).toEqual(first)
    expect(Object.isFrozen(first)).toBe(true)
    expect(Object.isFrozen(first.payload)).toBe(true)
  })

  it("isolates pending requests by task and clears only the completed task", () => {
    const storage = new MemoryStorage()
    ensurePendingTaskRerun("task_1", rerunPayload(), storage, () => "key_1")
    ensurePendingTaskRerun("task_2", rerunPayload(), storage, () => "key_2")

    clearPendingTaskRerun("task_1", storage)

    expect(loadPendingTaskRerun("task_1", storage)).toBeNull()
    expect(loadPendingTaskRerun("task_2", storage)?.idempotencyKey).toBe("key_2")
  })

  it("allows a stored request to confirm its result even when a new preflight is blocked", () => {
    expect(canSubmitTaskRerun({
      pendingRequest: {
        idempotencyKey: "key_1",
        payload: rerunPayload(),
      },
      preflightAllowed: false,
      reason: "",
      requiresSideEffectConfirmation: true,
      confirmSideEffects: false,
    })).toBe(true)
  })

  it("keeps the pending confirmation state visible while a fresh preflight is loading", () => {
    expect(shouldBlockTaskRerunFormForPreflight(true, {
      idempotencyKey: "key_1",
      payload: rerunPayload(),
    })).toBe(false)
    expect(shouldBlockTaskRerunFormForPreflight(true, null)).toBe(true)
  })

  it("degrades safely when the browser blocks access to sessionStorage", () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage")
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError")
      },
    })

    try {
      expect(loadPendingTaskRerun("task_1")).toBeNull()
      expect(() => ensurePendingTaskRerun("task_1", rerunPayload())).toThrow(
        "当前浏览器无法保存待确认的重跑请求",
      )
    } finally {
      if (originalDescriptor) {
        Object.defineProperty(globalThis, "sessionStorage", originalDescriptor)
      } else {
        delete (globalThis as { sessionStorage?: Storage }).sessionStorage
      }
    }
  })
})
