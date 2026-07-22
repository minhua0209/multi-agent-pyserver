import { readFileSync } from "node:fs"

import { describe, expect, it } from "vitest"


const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8")
const mainSource = readFileSync(new URL("./main.tsx", import.meta.url), "utf8")
const indexSource = readFileSync(new URL("../index.html", import.meta.url), "utf8")

function cssRule(selector: string, source = styles) {
  return cssRules(selector, source)[0] || ""
}

function cssRules(selector: string, source = styles) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  return Array.from(
    source.matchAll(new RegExp(`(?:^|[{}])\\s*${escaped}\\s*\\{([^{}]*)\\}`, "gm")),
    (match) => match[1],
  )
}

function atRuleContent(rule: string, source = styles) {
  const ruleStart = source.indexOf(rule)
  const openingBrace = source.indexOf("{", ruleStart + rule.length)
  if (ruleStart < 0 || openingBrace < 0) return ""

  let depth = 0
  for (let index = openingBrace; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1
    if (source[index] === "}") depth -= 1
    if (depth === 0) return source.slice(openingBrace + 1, index)
  }
  return ""
}

function sourceBetween(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start)
  const endIndex = source.indexOf(end, startIndex + start.length)
  if (startIndex < 0 || endIndex < 0) return ""
  return source.slice(startIndex, endIndex)
}

function lastCssDeclaration(rules: string[], property: string) {
  const escaped = property.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const declaration = new RegExp(`(?:^|;)\\s*${escaped}\\s*:\\s*([^;}]*)`, "g")
  const values = rules.flatMap((rule) => Array.from(rule.matchAll(declaration), (match) => match[1].trim()))
  return values.at(-1) || ""
}

const taskDetailModalSource = sourceBetween(appSource, "function TaskDetailModal(", "function TaskRerunControl(")
const executionGraphSource = sourceBetween(appSource, "function ExecutionGraph(", "function subtaskFailureReason(")


describe("task detail responsive styles", () => {
  it("constrains the current Ant Design modal container to the viewport", () => {
    const modalContainerRule = cssRule(
      ".task-detail-modal .ant-modal-container,\n.task-detail-modal .ant-modal-content",
    )

    expect(modalContainerRule).toMatch(/height:\s*100%/)
    expect(modalContainerRule).toMatch(/display:\s*flex/)
    expect(modalContainerRule).toMatch(/overflow:\s*hidden/)
    expect(taskDetailModalSource).not.toContain("minHeight: 760")
  })

  it("keeps the detail title shrinkable beside fixed header controls", () => {
    expect(cssRule(".task-detail-title .ant-typography:first-child")).toMatch(/flex:\s*1 1 auto/)
    expect(cssRule(".task-detail-title .ant-typography:first-child")).toMatch(/min-width:\s*0/)
    expect(cssRule(".task-detail-title .ant-btn")).toMatch(/flex:\s*0 0 auto/)
  })

  it("uses an icon-only rerun control on mobile", () => {
    const mobileStyles = atRuleContent("@media (max-width: 767px)")
    expect(cssRule(".task-detail-title .ant-btn", mobileStyles)).toMatch(/width:\s*32px/)
    expect(cssRule(".task-detail-title .ant-btn > span:not(.ant-btn-icon)", mobileStyles)).toMatch(
      /display:\s*none/,
    )
  })
})


describe("task detail execution layout requirements", () => {
  it("renders task detail status and section controls", () => {
    expect(taskDetailModalSource).toContain('className="task-question-icon"')
    expect(taskDetailModalSource).toContain("taskStatusColor(taskStatus(task))")
    expect(taskDetailModalSource).toContain('className="execution-section-header"')
  })

  it("renders expandable subtask output and independent actions", () => {
    expect(executionGraphSource).toContain('<details className="subtask-node-output">')
    expect(executionGraphSource).toContain('className="subtask-node-action-button"')
  })

  it("keeps execution sections sized by their content", () => {
    const executionSections = cssRules(".execution-section")

    expect(executionSections).not.toHaveLength(0)
    expect(executionSections.every((rule) => !/vh/.test(rule))).toBe(true)
    expect(executionSections.every((rule) => !/flex-basis:/.test(rule))).toBe(true)
    expect(executionSections.every((rule) => !/overflow:\s*hidden/.test(rule))).toBe(true)
    expect(lastCssDeclaration(executionSections, "flex")).toBe("0 0 auto")
    expect(lastCssDeclaration(executionSections, "max-height")).toBe("none")
    expect(lastCssDeclaration(executionSections, "overflow")).toBe("visible")
  })

  it("keeps the execution graph fluid on desktop and mobile", () => {
    const mobileStyles = atRuleContent("@media (max-width: 767px)")
    const executionScrollRules = cssRules(".execution-scroll")
    const graphRoundNodeRules = cssRules(".graph-round-node")
    const graphSubtaskRules = cssRules(".graph-subtasks")
    const parallelSubtaskRules = cssRules(".graph-subtasks.parallel")
    const mobileParallelSubtaskRules = cssRules(".graph-subtasks.parallel", mobileStyles)

    expect(executionScrollRules).not.toHaveLength(0)
    expect(lastCssDeclaration(executionScrollRules, "height")).toBe("auto")
    expect(lastCssDeclaration(executionScrollRules, "max-height")).toBe("none")
    expect(lastCssDeclaration(executionScrollRules, "overflow-x")).toBe("auto")
    expect(lastCssDeclaration(executionScrollRules, "overflow-y")).toBe("visible")
    expect(graphRoundNodeRules).not.toHaveLength(0)
    expect(lastCssDeclaration(graphRoundNodeRules, "width")).toBe("100%")
    expect(lastCssDeclaration(graphRoundNodeRules, "max-width")).toBe("1080px")
    expect(lastCssDeclaration(graphRoundNodeRules, "min-width")).toBe("0")
    expect(lastCssDeclaration(graphSubtaskRules, "align-items")).toBe("start")
    expect(parallelSubtaskRules).not.toHaveLength(0)
    expect(parallelSubtaskRules.some((rule) => /grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(240px,\s*1fr\)\)/.test(rule))).toBe(true)
    expect(mobileParallelSubtaskRules).not.toHaveLength(0)
    expect(lastCssDeclaration(mobileParallelSubtaskRules, "grid-template-columns")).toBe("1fr")
  })

  it("preserves readable subtask output and responsive feedback", () => {
    const subtaskOutputRules = cssRules(".subtask-node-output")
    const subtaskOutputBodyRules = cssRules(".subtask-node-output > div")
    const taskQuestionRules = cssRules(".task-four-question")
    const subtaskNodeRules = cssRules(".graph-subtask-node")

    expect(subtaskOutputRules).not.toHaveLength(0)
    expect(subtaskOutputRules.every((rule) => !/max-height:\s*42px/.test(rule))).toBe(true)
    expect(subtaskOutputBodyRules).not.toHaveLength(0)
    expect(lastCssDeclaration(subtaskOutputBodyRules, "white-space")).toBe("pre-wrap")
    expect(lastCssDeclaration(subtaskOutputBodyRules, "overflow-wrap")).toBe("anywhere")
    expect(taskQuestionRules).not.toHaveLength(0)
    expect(lastCssDeclaration(taskQuestionRules, "transition")).toMatch(/(?:transform|border-color|box-shadow)/)
    expect(subtaskNodeRules).not.toHaveLength(0)
    expect(lastCssDeclaration(subtaskNodeRules, "min-width")).toBe("0")
    expect(lastCssDeclaration(subtaskNodeRules, "transition")).toMatch(/(?:transform|border-color|box-shadow)/)
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
    const tabletStyles = atRuleContent("@media (max-width: 1024px)")
    const mobileStyles = atRuleContent("@media (max-width: 767px)")

    expect(cssRule(".overview-metric-grid", tabletStyles)).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
    expect(cssRule(".overview-metric-grid", mobileStyles)).toMatch(/grid-template-columns:\s*1fr/)
  })

  it("allows the toolbar to wrap on tablets", () => {
    const tabletStyles = atRuleContent("@media (max-width: 1024px)")

    expect(cssRule(".top-toolbar", tabletStyles)).toMatch(/flex-wrap:\s*wrap/)
    expect(cssRule(".top-toolbar > .ant-flex", tabletStyles)).toMatch(/flex-wrap:\s*wrap/)
  })

  it("resets Ant Layout fixed widths on mobile", () => {
    const mobileStyles = atRuleContent("@media (max-width: 767px)")

    expect(cssRule(".app-shell", mobileStyles)).toMatch(/display:\s*flex/)
    expect(cssRule(".app-shell", mobileStyles)).toMatch(/flex-direction:\s*column/)
    expect(cssRule(".app-shell.ant-layout-has-sider", mobileStyles)).toMatch(/flex-direction:\s*column/)
    expect(cssRule(".side-nav", mobileStyles)).toMatch(/width:\s*100%\s*!important/)
    expect(cssRule(".side-nav", mobileStyles)).toMatch(/min-width:\s*0\s*!important/)
    expect(cssRule(".app-shell > \.ant-layout", mobileStyles)).toMatch(/width:\s*100%\s*!important/)
    expect(cssRule(".content", mobileStyles)).toMatch(/min-width:\s*0/)
  })
})
