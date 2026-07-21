import { describe, expect, it } from "vitest"

import { initialPublishForm, validatePublishForm, validateWorkflowBuilderOpen } from "./publishForm"

describe("publish form defaults", () => {
  it("starts with empty task title and content", () => {
    expect(initialPublishForm()).toMatchObject({
      title: "",
      content: "",
      workflowId: "",
    })
  })

  it("returns a validation message for incomplete publish forms", () => {
    expect(validatePublishForm("", "任务诉求")).toBe("请填写任务名称")
    expect(validatePublishForm("任务名称", "")).toBe("请填写任务诉求")
    expect(validatePublishForm("超长任务名称".repeat(9), "任务诉求")).toBe("任务名称不能超过 50 个字")
    expect(validatePublishForm("任务名称", "任务诉求")).toBe("")
  })

  it("allows opening workflow builder before task fields are filled", () => {
    expect(validateWorkflowBuilderOpen("", "")).toBe("")
  })
})
