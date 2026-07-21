import { describe, expect, it } from "vitest"

import { isManualWorkflowTask, isTaskRerunnable, taskNodeText, taskStatusText, taskType, taskTypeText } from "./taskType"

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

  it("maps internal current node codes to Chinese labels", () => {
    expect(taskNodeText({ id: "task_1", current_node: "completion_judge" })).toBe("完成判断")
    expect(taskNodeText({ id: "task_2", current_node: "human_execution" })).toBe("人工处理")
    expect(taskNodeText({ id: "task_3", current_node: "dispatch_decision" })).toBe("智能分发")
  })

  it("uses task status context for common list node labels", () => {
    expect(taskNodeText({ id: "task_1", task_status: "succeeded", current_node: "completion_judge" })).toBe("已完成")
    expect(taskNodeText({ id: "task_2", task_status: "running", current_node: "human_confirmation" })).toBe("待确认任务清单")
    expect(taskNodeText({ id: "task_3", task_status: "running", current_node: "human_execution" })).toBe("等待人工处理")
    expect(
      taskNodeText({
        id: "task_4",
        task_type: "manual_orchestration",
        task_status: "running",
        current_node: "dispatch_decision",
      }),
    ).toBe("流程执行中")
  })

  it("maps blocked tasks to a human intervention state", () => {
    expect(taskStatusText("blocked")).toBe("待人工介入")
  })

  it("allows terminal tasks with an active execution to be rerun", () => {
    expect(isTaskRerunnable({ id: "task_failed", task_status: "failed", active_execution_id: "execution_1" })).toBe(true)
    expect(isTaskRerunnable({ id: "task_blocked", task_status: "blocked", active_execution_id: "execution_1" })).toBe(true)
    expect(isTaskRerunnable({ id: "task_running", task_status: "running", active_execution_id: "execution_1" })).toBe(false)
  })
})
