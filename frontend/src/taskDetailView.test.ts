import { describe, expect, it } from "vitest"

import { Task, WorkflowDefinition } from "./api/taskhub"
import { manualWorkflowFlowElements, taskDetailSummaryBlocks, taskDetailTypeBadge } from "./taskDetailView"

describe("task detail view helpers", () => {
  const definition: WorkflowDefinition = {
    nodes: [
      { id: "start", type: "start", title: "开始", description: "初始化上下文" },
      { id: "agent_1", type: "agent", title: "需求分析 Agent", description: "梳理需求" },
      { id: "end", type: "end", title: "完成", description: "汇总输出" },
    ],
    edges: [
      { from: "start", to: "agent_1", condition: {} },
      { from: "agent_1", to: "end", condition: {} },
    ],
  }

  const task: Task = {
    id: "task_1",
    title: "客户需求协同处理",
    content: "请分析客户需求",
    task_type: "manual_orchestration",
    task_status: "succeeded",
    request_metadata: {
      workflow_name: "客户需求协同处理",
      workflow_definition: definition,
    },
    draft: {
      title: "客户需求协同处理",
      description: "请分析客户需求",
    },
    context: {
      rounds: [
        {
          id: "round_1",
          round_index: 1,
          execution_mode: "workflow_template",
          subtasks: [
            {
              id: "task_1_agent_1",
              title: "需求分析 Agent",
              status: "succeeded",
              output: "已识别需求范围。",
            },
          ],
        },
      ],
    },
  }

  it("keeps task type as a compact badge instead of a summary block", () => {
    expect(taskDetailSummaryBlocks(task).map((block) => block.title)).toEqual(["原始诉求", "任务清单"])
    expect(taskDetailTypeBadge(task)).toEqual({ text: "手动编排", color: "purple" })
  })

  it("maps manual workflow definition to readonly React Flow elements with node state", () => {
    const result = manualWorkflowFlowElements(task, definition)

    expect(result.nodes.map((node) => node.id)).toEqual(["start", "agent_1", "end"])
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["start", "agent_1"],
      ["agent_1", "end"],
    ])
    expect(result.nodes.find((node) => node.id === "agent_1")?.data).toMatchObject({
      status: "succeeded",
      statusText: "已完成",
      output: "已识别需求范围。",
    })
    expect(result.nodes.find((node) => node.id === "end")?.data).toMatchObject({
      status: "succeeded",
      statusText: "已完成",
    })
  })
})
