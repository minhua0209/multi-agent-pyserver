import { describe, expect, it } from "vitest"

import { TOAST_DISMISS_MS, createToastMessage, shouldDismissToast } from "./toastState"

describe("toast state", () => {
  it("creates a dismissible toast message with stable id and text", () => {
    expect(createToastMessage(" 用户已新增 ", 7)).toEqual({ id: 7, text: "用户已新增" })
    expect(createToastMessage("", 8)).toBeNull()
  })

  it("dismisses only the toast that scheduled the timer", () => {
    expect(shouldDismissToast({ id: 7, text: "用户已新增" }, 7)).toBe(true)
    expect(shouldDismissToast({ id: 8, text: "用户已新增" }, 7)).toBe(false)
    expect(shouldDismissToast(null, 7)).toBe(false)
  })

  it("keeps toast visible briefly before fade-out finishes", () => {
    expect(TOAST_DISMISS_MS).toBeGreaterThanOrEqual(2400)
    expect(TOAST_DISMISS_MS).toBeLessThanOrEqual(3600)
  })
})
