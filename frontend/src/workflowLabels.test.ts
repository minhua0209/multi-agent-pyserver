import { describe, expect, it } from "vitest"

import { capabilityLabel } from "./workflowLabels"

describe("workflow labels", () => {
  it("renders capability filter labels in Chinese without changing values", () => {
    expect(capabilityLabel("all")).toBe("全部能力")
    expect(capabilityLabel("general_processing")).toBe("通用处理")
    expect(capabilityLabel("write_report")).toBe("报告撰写")
    expect(capabilityLabel("send_email")).toBe("发送邮件")
    expect(capabilityLabel("requirement_analysis")).toBe("需求分析")
    expect(capabilityLabel("monitoring_alerting")).toBe("监控告警")
    expect(capabilityLabel("quality_gate")).toBe("质量门禁")
  })

  it("falls back to readable text for unknown capability values", () => {
    expect(capabilityLabel("custom_tool_chain")).toBe("custom tool chain")
  })
})
