import { describe, expect, it } from "vitest"

import { draftDescriptionValue, draftTitleValue, taskLabel } from "../src/intentDrafts.ts"

describe("intent draft helpers", () => {
  it("uses the user task title and model task-list description", () => {
    const task = {
      id: "task_1",
      title: "用户输入的任务名称",
      description: "用户输入的任务诉求",
      draft: {
        title: "模型拆解出的长标题",
        description: "模型拆解出的任务清单",
      },
    }

    expect(taskLabel()).toBe("任务")
    expect(draftTitleValue(task)).toBe("用户输入的任务名称")
    expect(draftDescriptionValue(task)).toBe("模型拆解出的任务清单")
  })
})
