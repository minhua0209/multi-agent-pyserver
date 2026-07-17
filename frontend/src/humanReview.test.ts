import { describe, expect, it } from "vitest"

import { humanReviewDocumentSourceLabel, humanReviewDocumentText } from "./humanReview"

describe("human review helpers", () => {
  it("uses upstream agent output as the document that needs human review", () => {
    expect(
      humanReviewDocumentText({
        upstream_outputs: ["用户故事拆解 Agent: ## 用户故事拆解报告\n\n请审核这份报告"],
        task_context_summary: "旧上下文",
      }),
    ).toBe("用户故事拆解 Agent: ## 用户故事拆解报告\n\n请审核这份报告")
  })

  it("falls back to task context summary when no upstream output exists", () => {
    expect(
      humanReviewDocumentText({
        task_context_summary: "## 汇总报告\n\n暂无独立上游输出",
      }),
    ).toBe("## 汇总报告\n\n暂无独立上游输出")
  })

  it("labels where the review document comes from", () => {
    expect(humanReviewDocumentSourceLabel({ upstream_outputs: ["agent output"] })).toBe("上游产出")
    expect(humanReviewDocumentSourceLabel({ task_context_summary: "context summary" })).toBe("上下文")
    expect(humanReviewDocumentSourceLabel({})).toBe("暂无文档")
  })
})
