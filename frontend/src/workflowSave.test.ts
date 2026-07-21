import { describe, expect, it } from "vitest"

import { workflowBuilderCopy, workflowTemplateSaveAction } from "./workflowSave"

const definition = {
  nodes: [{ id: "start", type: "start" }],
  edges: [],
}

describe("workflow template saving", () => {
  it("creates a workflow template when the name does not exist", () => {
    expect(
      workflowTemplateSaveAction(
        [{ id: "workflow_001", name: "已有流程", definition: { nodes: [], edges: [] } }],
        {
          name: "新流程",
          description: "新的流程说明",
          definition,
        },
      ),
    ).toEqual({
      type: "create",
      payload: {
        name: "新流程",
        description: "新的流程说明",
        definition,
      },
    })
  })

  it("updates and overwrites a workflow template when the name already exists", () => {
    expect(
      workflowTemplateSaveAction(
        [{ id: "workflow_001", name: "客户交付流程", definition: { nodes: [], edges: [] } }],
        {
          name: " 客户交付流程 ",
          description: "覆盖后的流程说明",
          definition,
        },
      ),
    ).toEqual({
      type: "update",
      workflowId: "workflow_001",
      payload: {
        name: "客户交付流程",
        description: "覆盖后的流程说明",
        definition,
      },
    })
  })

  it("uses workflow node copy and has no direct task submit label", () => {
    expect(workflowBuilderCopy.title).toBe("流程节点编排")
    expect(workflowBuilderCopy.saveButton).toBe("保存流程模板")
    expect(Object.values(workflowBuilderCopy)).not.toContain("提交任务")
  })
})
