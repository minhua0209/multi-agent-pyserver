import { describe, expect, it } from "vitest"

import { Task, WorkflowDefinition } from "./api/taskhub"
import {
  compactContextText,
  manualWorkflowFlowElements,
  taskContextNodeView,
  taskDetailSummaryBlocks,
  taskDetailTypeBadge,
} from "./taskDetailView"

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

  it("lays out long manual workflows with clear rows instead of squeezing every node into one line", () => {
    const longDefinition: WorkflowDefinition = {
      nodes: [
        { id: "start", type: "start", title: "开始" },
        { id: "agent_1", type: "agent", title: "需求分析 Agent" },
        { id: "agent_2", type: "agent", title: "技术方案 Agent" },
        { id: "agent_3", type: "agent", title: "接口设计 Agent" },
        { id: "agent_4", type: "agent", title: "后端研发 Agent" },
        { id: "agent_5", type: "agent", title: "代码评审 Agent" },
        { id: "end", type: "end", title: "完成" },
      ],
      edges: [
        { from: "start", to: "agent_1", condition: {} },
        { from: "agent_1", to: "agent_2", condition: {} },
        { from: "agent_2", to: "agent_3", condition: {} },
        { from: "agent_3", to: "agent_4", condition: {} },
        { from: "agent_4", to: "agent_5", condition: {} },
        { from: "agent_5", to: "end", condition: {} },
      ],
    }

    const result = manualWorkflowFlowElements({ ...task, task_status: "running" }, longDefinition)
    const positions = Object.fromEntries(result.nodes.map((node) => [node.id, node.position]))

    expect(result.nodes[0].style).toMatchObject({ width: 260 })
    expect(positions.start).toEqual({ x: 80, y: 80 })
    expect(positions.agent_1).toEqual({ x: 420, y: 80 })
    expect(positions.agent_2).toEqual({ x: 760, y: 80 })
    expect(positions.agent_3).toEqual({ x: 80, y: 290 })
    expect(positions.agent_4).toEqual({ x: 420, y: 290 })
    expect(positions.agent_5).toEqual({ x: 760, y: 290 })
    expect(positions.end).toEqual({ x: 80, y: 500 })
  })

  it("builds compact node context previews for collapsible detail cards", () => {
    expect(compactContextText("第一行\n第二行   很长很长很长", 10)).toBe("第一行 第二行...")
    expect(
      taskContextNodeView({
        id: "subtask_1",
        title: "需求分析 Agent",
        assignee_type: "agent",
        assigned_agent_id: "agent_requirement",
        status: "succeeded",
        description: "梳理业务诉求、目标用户和边界。",
        output: "结论：需求缺少业务背景，需要补充客户画像、业务流程和验收标准。",
      }),
    ).toMatchObject({
      title: "需求分析 Agent",
      typeText: "Agent",
      assigneeText: "agent_requirement",
      preview: "结论：需求缺少业务背景，需要补充客户画像、业务流程和验收标准。",
      hasDetail: true,
    })
    expect(
      taskContextNodeView({
        id: "human_1",
        title: "",
        assignee_type: "human",
        assignee_user_name: "李晨",
        status: "running",
        description: "请人工确认折扣是否通过。",
      }),
    ).toMatchObject({
      title: "human_1",
      typeText: "人工",
      assigneeText: "李晨",
      preview: "请人工确认折扣是否通过。",
    })
  })
})
