import { describe, expect, it } from "vitest"

import { buildWorkflowDefinition } from "./workflowBuilder"

describe("buildWorkflowDefinition", () => {
  it("builds parallel agent branches, condition routes, and revision loop", () => {
    const definition = buildWorkflowDefinition([
      {
        id: "agent_requirement",
        name: "需求分析 Agent",
        description: "提取客户目标",
        capabilities: ["analysis"],
      },
      {
        id: "agent_risk",
        name: "风险识别 Agent",
        description: "识别交付风险",
        capabilities: ["risk"],
      },
      {
        id: "agent_data",
        name: "数据分析 Agent",
        description: "整理指标证据",
        capabilities: ["data"],
      },
    ], {
      id: "agent_revise",
      name: "返工修订 Agent",
      description: "根据人工意见返工",
      capabilities: ["writing"],
    })

    expect(definition.nodes.map((node) => node.id)).toEqual([
      "start",
      "parallel_agent_1",
      "parallel_agent_2",
      "parallel_agent_3",
      "review",
      "judge",
      "end",
      "revise",
    ])
    expect(definition.nodes.find((node) => node.id === "parallel_agent_2")?.agent_id).toBe("agent_risk")
    expect(definition.nodes.find((node) => node.id === "judge")?.config).toMatchObject({
      mode: "rule",
      source_node_id: "review",
      field: "decision",
      allowed_decisions: ["approved", "rejected", "need_more_info"],
      default_decision: "need_more_info",
    })
    expect(definition.edges).toEqual([
      { from: "start", to: "parallel_agent_1", condition: {} },
      { from: "start", to: "parallel_agent_2", condition: {} },
      { from: "start", to: "parallel_agent_3", condition: {} },
      { from: "parallel_agent_1", to: "review", condition: {} },
      { from: "parallel_agent_2", to: "review", condition: {} },
      { from: "parallel_agent_3", to: "review", condition: {} },
      { from: "review", to: "judge", condition: {} },
      { from: "judge", to: "end", condition: { type: "decision", value: "approved" } },
      { from: "judge", to: "revise", condition: { type: "decision", value: "rejected" } },
      { from: "revise", to: "review", condition: {} },
    ])
  })
})
