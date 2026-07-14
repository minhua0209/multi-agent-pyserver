// =======================================
// ANNOTATION DATA -- 需求标注锚点骨架
// anchorIds：列出 Demo 中所有 data-annotation-id。
// annotationData：由后续需求标注步骤填充。
// =======================================

const annotationTypeLabels = {
  page: "页面",
  region: "区域",
  tab: "Tab",
  drawer: "侧边抽屉",
  modal: "弹窗",
  result: "结果区",
  empty: "空状态",
  loading: "加载态",
  error: "错误态",
  reserved: "预留能力",
}

const anchorIds = [
  "app-shell",
  "side-navigation",
  "top-toolbar",
  "role-switcher",
  "global-search",
  "toast-region",

  "overview-page",
  "overview-filter-bar",
  "overview-metric-strip",
  "overview-cockpit-health",
  "overview-command-layout",
  "overview-risk-panel",
  "overview-flow-panel",
  "overview-node-matrix",
  "overview-agent-coverage",
  "overview-recent-task-table",
  "overview-automation-panel",
  "overview-recent-event-list",
  "overview-empty-state",

  "publish-page",
  "publish-request-form",
  "publish-source-select",
  "publish-content-field",
  "publish-metadata-fields",
  "publish-submit-result",
  "publish-form-loading",
  "publish-form-error",

  "confirmation-page",
  "confirmation-queue-list",
  "confirmation-original-request",
  "confirmation-draft-editor",
  "confirmation-evidence-panel",
  "confirmation-disabled-actions",
  "confirmation-confirm-bar",
  "confirmation-empty-state",
  "confirmation-validation-error",

  "tasks-page",
  "tasks-filter-bar",
  "tasks-table",
  "tasks-search-result",
  "tasks-empty-state",
  "tasks-filter-empty",
  "tasks-loading-state",
  "tasks-error-state",

  "task-detail-page",
  "task-detail-header",
  "task-detail-objective",
  "task-detail-current-context",
  "task-detail-tabs",
  "task-tab-rounds",
  "task-tab-subtasks",
  "task-tab-events",
  "task-rounds-table",
  "task-subtask-table",
  "task-event-timeline",
  "task-result-form",
  "task-reserved-actions",
  "task-permission-note",

  "agents-page",
  "agents-filter-bar",
  "agents-table",
  "agent-register-entry",
  "agent-tool-summary",
  "agents-empty-state",
  "agents-filter-empty",

  "audit-page",
  "audit-filter-bar",
  "audit-event-table",
  "audit-event-detail-panel",
  "audit-export-reserved",
  "audit-empty-state",
  "audit-permission-state",

  "governance-page",
  "governance-runtime-mode",
  "governance-rule-list",
  "governance-source-permission-reserved",
  "governance-kill-switch-reserved",
  "governance-audit-link",

  "task-detail-drawer",
  "agent-detail-drawer",
  "event-detail-drawer",
  "confirm-task-modal",
  "submit-result-modal",
  "register-agent-modal",
  "reserved-action-modal",
]

const annotationData = []

window.PRODUCT_PIPELINE_ANNOTATIONS = {
  annotationTypeLabels,
  anchorIds,
  annotationData,
}

window.GT_FLOW_ANNOTATIONS = window.PRODUCT_PIPELINE_ANNOTATIONS
