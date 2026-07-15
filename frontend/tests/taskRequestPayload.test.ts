import { describe, expect, it } from "vitest"

import { buildTaskRequestPayload } from "../src/api/taskhub.ts"

describe("buildTaskRequestPayload", () => {
  it("builds a normal task request payload", () => {
    expect(buildTaskRequestPayload("任务名称", "任务诉求")).toEqual({
      source_type: "business_system",
      title: "任务名称",
      content: "任务诉求",
      metadata: {},
    })
  })

  it("builds a task request payload with a workflow id", () => {
    expect(buildTaskRequestPayload("任务名称", "任务诉求", "workflow_1")).toEqual({
      source_type: "business_system",
      title: "任务名称",
      content: "任务诉求",
      metadata: {
        execution_mode: "workflow_template",
        workflow_id: "workflow_1",
      },
    })
  })

  it("builds a task request payload with an inline workflow definition", () => {
    expect(
      buildTaskRequestPayload("任务名称", "任务诉求", {
        execution_mode: "workflow_template",
        workflow_name: "客户交付 Workflow",
        workflow_definition: {
          nodes: [{ id: "start", type: "start", title: "开始", description: "", config: {} }],
          edges: [],
        },
      }),
    ).toEqual({
      source_type: "business_system",
      title: "任务名称",
      content: "任务诉求",
      metadata: {
        execution_mode: "workflow_template",
        workflow_name: "客户交付 Workflow",
        workflow_definition: {
          nodes: [{ id: "start", type: "start", title: "开始", description: "", config: {} }],
          edges: [],
        },
      },
    })
  })
})
