import { describe, expect, it } from "vitest"

import {
  buildWorkflowEdgePaths,
  clampNodePosition,
  defaultWorkflowNodePositions,
  expandCanvasSizeForNode,
  nodeSize,
  removeWorkflowNode,
} from "./workflowCanvas"

describe("workflow canvas helpers", () => {
  it("clamps dragged nodes inside the canvas", () => {
    expect(clampNodePosition({ left: -40, top: -20 }, { width: 160, height: 104 }, { width: 960, height: 640 })).toEqual({
      left: 0,
      top: 0,
    })
    expect(clampNodePosition({ left: 940, top: 630 }, { width: 160, height: 104 }, { width: 960, height: 640 })).toEqual({
      left: 800,
      top: 536,
    })
  })

  it("recalculates edge paths when a node is moved", () => {
    const original = buildWorkflowEdgePaths(defaultWorkflowNodePositions)
    const moved = buildWorkflowEdgePaths({
      ...defaultWorkflowNodePositions,
      start: { ...defaultWorkflowNodePositions.start, left: defaultWorkflowNodePositions.start.left + 80 },
    })

    expect(original.find((edge) => edge.id === "start-parallel_agent_1")?.path).not.toEqual(
      moved.find((edge) => edge.id === "start-parallel_agent_1")?.path,
    )
    expect(moved.find((edge) => edge.id === "judge-revise")?.className).toBe("workflow-edge rejected")
  })

  it("builds paths from the current canvas edges", () => {
    const paths = buildWorkflowEdgePaths(defaultWorkflowNodePositions, [
      { from: "start", to: "review", condition: {} },
      { from: "judge", to: "revise", condition: { type: "decision", value: "rejected" } },
    ])

    expect(paths.map((path) => path.id)).toEqual(["start-review", "judge-revise"])
    expect(paths[1].className).toBe("workflow-edge rejected")
  })

  it("removes a canvas node with its connected edges", () => {
    const result = removeWorkflowNode(
      [
        { id: "start", type: "start" },
        { id: "agent_a", type: "agent", title: "A" },
        { id: "end", type: "end" },
      ],
      [
        { from: "start", to: "agent_a", condition: {} },
        { from: "agent_a", to: "end", condition: {} },
      ],
      "agent_a",
    )

    expect(result.nodes.map((node) => node.id)).toEqual(["start", "end"])
    expect(result.edges).toEqual([])
  })

  it("uses condition dimensions for dynamically added condition nodes", () => {
    expect(nodeSize("condition_1")).toEqual(nodeSize("judge"))
  })

  it("expands canvas bounds when a node moves past the current edge", () => {
    expect(expandCanvasSizeForNode({ width: 960, height: 640 }, { left: 900, top: 700 }, { width: 160, height: 104 })).toEqual({
      width: 1420,
      height: 1164,
    })
    expect(expandCanvasSizeForNode({ width: 960, height: 640 }, { left: 100, top: 120 }, { width: 160, height: 104 })).toEqual({
      width: 960,
      height: 640,
    })
  })
})
