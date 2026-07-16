import { describe, expect, it } from "vitest"

import { isManualWorkflowTask, taskType, taskTypeText } from "./taskType"

describe("task type helpers", () => {
  it("uses explicit task_type for task list labels", () => {
    expect(taskTypeText({ id: "task_1", task_type: "manual_orchestration" })).toBe("手动编排")
    expect(taskTypeText({ id: "task_2", task_type: "auto_planning" })).toBe("自动规划")
  })

  it("keeps old workflow_template tasks as manual orchestration", () => {
    const task = {
      id: "task_legacy",
      request_metadata: {
        execution_mode: "workflow_template",
      },
    }

    expect(taskType(task)).toBe("manual_orchestration")
    expect(isManualWorkflowTask(task)).toBe(true)
  })
})
