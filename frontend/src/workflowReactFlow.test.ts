import { describe, expect, it } from "vitest"

import { applyNodeInstruction, workflowToReactFlow } from "./workflowReactFlow"

describe("workflowReactFlow helpers", () => {
  it("maps workflow nodes and edges to React Flow elements", () => {
    const result = workflowToReactFlow(
      {
        nodes: [
          { id: "start", type: "start", title: "开始" },
          { id: "agent_1", type: "agent", title: "合同 Agent", agent_id: "agent_contract" },
          { id: "end", type: "end", title: "完成" },
        ],
        edges: [
          { from: "start", to: "agent_1", condition: {} },
          { from: "agent_1", to: "end", condition: {} },
        ],
      },
      {
        start: { left: 0, top: 120 },
        agent_1: { left: 260, top: 120 },
        end: { left: 520, top: 120 },
      },
    )

    expect(result.nodes[1]).toMatchObject({
      id: "agent_1",
      type: "workflowNode",
      position: { x: 260, y: 120 },
      data: { title: "合同 Agent", kind: "agent", agentId: "agent_contract" },
    })
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["start", "agent_1"],
      ["agent_1", "end"],
    ])
  })

  it("stores execution instructions on an agent node config", () => {
    const definition = applyNodeInstruction(
      {
        nodes: [
          { id: "start", type: "start" },
          { id: "agent_1", type: "agent", title: "合同 Agent", config: { context_inputs: ["task.content"] } },
        ],
        edges: [{ from: "start", to: "agent_1", condition: {} }],
      },
      "agent_1",
      "重点检查合同风险，输出风险等级。",
    )

    expect(definition.nodes.find((node) => node.id === "agent_1")?.config).toMatchObject({
      context_inputs: ["task.content"],
      execution_instruction: "重点检查合同风险，输出风险等级。",
    })
  })
})
