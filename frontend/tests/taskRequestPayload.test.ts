import assert from "node:assert/strict"

import { buildTaskRequestPayload } from "../src/api/taskhub.ts"

assert.deepEqual(buildTaskRequestPayload("任务名称", "任务诉求"), {
  source_type: "business_system",
  title: "任务名称",
  content: "任务诉求",
  metadata: {},
})

assert.deepEqual(buildTaskRequestPayload("任务名称", "任务诉求", "workflow_1"), {
  source_type: "business_system",
  title: "任务名称",
  content: "任务诉求",
  metadata: {
    execution_mode: "workflow_template",
    workflow_id: "workflow_1",
  },
})
