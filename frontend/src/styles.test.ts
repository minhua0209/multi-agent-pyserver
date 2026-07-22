import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"


const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8")
const mainSource = readFileSync(new URL("./main.tsx", import.meta.url), "utf8")
const indexSource = readFileSync(new URL("../index.html", import.meta.url), "utf8")

function cssRule(selector: string, source = styles) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  return source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] || ""
}

function cssRules(selector: string, source = styles) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  return Array.from(source.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "g")), (match) => match[1])
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


describe("theme toggle", () => {
  it("applies the persisted theme before React renders", () => {
    expect(indexSource).toMatch(/localStorage\.getItem\("taskhub-theme"\)/)
    expect(indexSource).toMatch(/document\.documentElement\.dataset\.theme\s*=/)
    expect(mainSource).not.toMatch(/readThemePreference/)
  })

  it("switches both Ant Design and the application theme", () => {
    expect(appSource).toMatch(/antdTheme\.darkAlgorithm/)
    expect(appSource).toMatch(/antdTheme\.defaultAlgorithm/)
    expect(appSource).toMatch(/toggleTheme\(/)
    expect(appSource).toContain("切换到浅色模式")
    expect(appSource).toContain("切换到深色模式")
  })

  it("defines light surfaces and theme-driven application shells", () => {
    expect(cssRule('html[data-theme="light"]')).toMatch(/--surface:\s*#f4f7fb/)
    expect(cssRule('html[data-theme="light"]')).toMatch(/--surface-panel:\s*#ffffff/)
    expect(cssRule('html[data-theme="light"]')).toMatch(/--accent:\s*#0f766e/)
    expect(cssRule('html[data-theme="light"]')).toMatch(/--text-muted:\s*#5f6f85/)
    expect(cssRule('html[data-theme="light"]')).toMatch(/--primary-contrast:\s*#ffffff/)
    expect(cssRule('html[data-theme="light"]')).toMatch(/--page-background:/)
    expect(appSource).toMatch(/colorPrimary:\s*isDarkTheme\s*\?\s*"#22c7b8"\s*:\s*"#0f766e"/)
    expect(cssRule("body")).toMatch(/background:\s*var\(--page-background\)/)
    expect(cssRule(".side-nav")).toMatch(/background:\s*var\(--nav-background\)\s*!important/)
    expect(cssRule(".top-toolbar")).toMatch(/background:\s*var\(--toolbar-background\)/)
    expect(cssRule(".content")).toMatch(/background:\s*var\(--page-background\)/)
    expect(cssRule(".workflow-canvas-stage")).toMatch(/var\(--canvas-background\)/)
    expect(cssRule(".graph-terminal")).toMatch(/color:\s*var\(--accent-text\)/)
    expect(cssRule(".human-review-document header > div")).toMatch(/color:\s*var\(--accent-text\)/)
    expect(cssRule(".human-subtask-card > div:first-child")).toMatch(/color:\s*var\(--accent-text\)/)
    expect(cssRule(".human-context-panel header > div,\n.human-context-title")).toMatch(/color:\s*var\(--accent-text\)/)
    expect(cssRule(".workflow-config-summary span")).toMatch(/color:\s*var\(--accent-text\)/)
    expect(cssRule(".task-detail-workflow-node")).toMatch(/box-shadow:\s*var\(--shadow\)/)
    expect(cssRule(".manual-workflow-node")).toMatch(/box-shadow:\s*var\(--shadow\)/)
    expect(cssRule(".graph-node,\n.graph-round-node")).toMatch(/box-shadow:\s*var\(--shadow\)/)
    expect(cssRule(".human-review-actions")).toMatch(/box-shadow:\s*var\(--shadow\)/)
    expect(cssRule(".btn-primary")).toMatch(/color:\s*var\(--primary-contrast\)/)
    expect(cssRules(".workflow-canvas-tools .btn-primary")).not.toHaveLength(0)
    expect(cssRules(".workflow-canvas-tools .btn-primary").every((rule) => /color:\s*var\(--primary-contrast\)/.test(rule))).toBe(true)
    expect(appSource).toContain('<Background color="var(--canvas-dot)"')
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

  it("allows the toolbar to wrap on tablets", () => {
    const tabletStyles = styles.slice(styles.indexOf("@media (max-width: 1024px)"))

    expect(cssRule(".top-toolbar", tabletStyles)).toMatch(/flex-wrap:\s*wrap/)
    expect(cssRule(".top-toolbar > .ant-flex", tabletStyles)).toMatch(/flex-wrap:\s*wrap/)
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
