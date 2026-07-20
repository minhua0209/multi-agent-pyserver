import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"


const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8")

function cssRule(selector: string, source = styles) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  return source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] || ""
}


describe("task detail responsive styles", () => {
  it("keeps the detail title shrinkable beside fixed header controls", () => {
    expect(cssRule(".task-detail-title .ant-typography:first-child")).toMatch(/flex:\s*1 1 auto/)
    expect(cssRule(".task-detail-title .ant-typography:first-child")).toMatch(/min-width:\s*0/)
    expect(cssRule(".task-detail-title .ant-btn")).toMatch(/flex:\s*0 0 auto/)
  })

  it("uses an icon-only rerun control on mobile", () => {
    const mobileStyles = styles.slice(styles.indexOf("@media (max-width: 767px)"))
    expect(cssRule(".task-detail-title .ant-btn", mobileStyles)).toMatch(/width:\s*32px/)
    expect(cssRule(".task-detail-title .ant-btn > span:not(.ant-btn-icon)", mobileStyles)).toMatch(
      /display:\s*none/,
    )
  })
})
