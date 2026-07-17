import { describe, expect, it } from "vitest"

import { workflowResourcePanelClass, workflowResourceToggleLabel } from "./workflowResourcePanel"

describe("workflow resource panel view state", () => {
  it("switches sidebar class and toggle label when collapsed", () => {
    expect(workflowResourcePanelClass(false)).toBe("workflow-left-drawers")
    expect(workflowResourceToggleLabel(false)).toBe("收起节点列表")

    expect(workflowResourcePanelClass(true)).toBe("workflow-left-drawers collapsed")
    expect(workflowResourceToggleLabel(true)).toBe("展开节点列表")
  })
})
