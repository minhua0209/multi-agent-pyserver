import { describe, expect, it } from "vitest"

import { workflowTemplateCardView } from "./workflowTemplateCard"

describe("workflow template card view", () => {
  it("builds readable card copy for saved workflow templates", () => {
    expect(
      workflowTemplateCardView({
        id: "workflow_1",
        name: "上线发布流程",
        description: "",
        status: "active",
        created_at: "2026-07-16T00:00:00Z",
        updated_at: "2026-07-16T00:00:00Z",
        definition: {
          nodes: [
            { id: "start", type: "start" },
            { id: "release", type: "agent" },
            { id: "end", type: "end" },
          ],
          edges: [
            { from: "start", to: "release" },
            { from: "release", to: "end" },
          ],
        },
      }),
    ).toEqual({
      title: "上线发布流程",
      description: "暂无描述",
      statusLabel: "启用",
      nodeCountLabel: "3 个节点",
      edgeCountLabel: "2 条连线",
    })
  })
})
