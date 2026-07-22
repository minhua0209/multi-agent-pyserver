import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"


const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8")

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


describe("dark application theme", () => {
  it("uses the Ant Design dark algorithm", () => {
    expect(appSource).toMatch(/(?:theme|antdTheme)\.darkAlgorithm/)
  })

  it("defines dark root surfaces without decorative radial gradients", () => {
    expect(cssRule(":root")).toMatch(/--surface:\s*#080f1d/)
    expect(cssRule(":root")).toMatch(/--surface-panel:\s*#121c2d/)
    expect(cssRule("body")).not.toMatch(/radial-gradient/)
  })

  it("keeps review and workflow execution surfaces dark", () => {
    expect(cssRule(".manual-workflow-node")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".task-detail-workflow-node")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".graph-node,\n.graph-round-node")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".graph-subtask-node")).toMatch(/background:\s*var\(--surface-muted\)/)
    expect(cssRule(".human-subtask-detail > div")).toMatch(/background:\s*var\(--surface-muted\)/)
    expect(cssRule(".human-review-document")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".human-review-document pre")).toMatch(/background:\s*var\(--surface-muted\)/)
    expect(cssRule(".human-subtask-card")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".human-context-panel")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".human-context-panel .ant-descriptions-view")).toMatch(/background:\s*var\(--surface-muted\)/)
    expect(cssRule(".human-context-output-list pre")).toMatch(/background:\s*var\(--surface-muted\)/)
    expect(cssRule(".human-review-actions")).toMatch(/background:\s*var\(--surface-panel\)/)
    expect(cssRule(".round-reason p")).toMatch(/background:\s*var\(--surface-muted\)/)
  })
})


describe("overview dashboard layout", () => {
  it("shows explicit empty states for status and trend panels", () => {
    expect(appSource).toContain('<EmptyState text="当前范围暂无状态数据" />')
    expect(appSource).toContain('<EmptyState text="当前范围暂无趋势数据" />')
  })

  it("uses four desktop metric columns", () => {
    expect(cssRule(".overview-metric-grid")).toMatch(/grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/)
  })

  it("uses two tablet columns and one mobile column", () => {
    const tabletStyles = styles.slice(styles.indexOf("@media (max-width: 1024px)"))
    const mobileStyles = styles.slice(styles.indexOf("@media (max-width: 767px)"))

    expect(cssRule(".overview-metric-grid", tabletStyles)).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
    expect(cssRule(".overview-metric-grid", mobileStyles)).toMatch(/grid-template-columns:\s*1fr/)
  })

  it("resets Ant Layout fixed widths on mobile", () => {
    const mobileStyles = styles.slice(styles.indexOf("@media (max-width: 767px)"))

    expect(cssRule(".app-shell", mobileStyles)).toMatch(/display:\s*flex/)
    expect(cssRule(".app-shell", mobileStyles)).toMatch(/flex-direction:\s*column/)
    expect(cssRule(".app-shell.ant-layout-has-sider", mobileStyles)).toMatch(/flex-direction:\s*column/)
    expect(cssRule(".side-nav", mobileStyles)).toMatch(/width:\s*100%\s*!important/)
    expect(cssRule(".side-nav", mobileStyles)).toMatch(/min-width:\s*0\s*!important/)
    expect(cssRule(".app-shell > \.ant-layout", mobileStyles)).toMatch(/width:\s*100%\s*!important/)
    expect(cssRule(".content", mobileStyles)).toMatch(/min-width:\s*0/)
  })
})
