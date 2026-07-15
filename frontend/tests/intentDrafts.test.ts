import assert from "node:assert/strict"

import { draftDescriptionValue, draftTitleValue, taskLabel } from "../src/intentDrafts.ts"

const task = {
  id: "task_1",
  title: "用户输入的任务名称",
  description: "用户输入的任务诉求",
  draft: {
    title: "模型拆解出的长标题",
    description: "模型拆解出的任务清单",
  },
}

assert.equal(taskLabel(), "任务")
assert.equal(draftTitleValue(task), "用户输入的任务名称")
assert.equal(draftDescriptionValue(task), "模型拆解出的任务清单")
