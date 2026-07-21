import { SubTask, Task, WorkflowDefinition } from "./api/taskhub"
import { isManualWorkflowTask, taskTypeText } from "./taskType"
import { WorkflowReactFlowEdge, WorkflowReactFlowNode, autoLayoutWorkflowNodePositions, workflowToReactFlow } from "./workflowReactFlow"

export interface TaskDetailSummaryBlock {
  key: "request" | "draft"
  title: string
  text: string
}

export interface TaskFourQuestion {
  key: "creator" | "goal" | "deliverable" | "completion"
  title: string
  text: string
}

export interface TaskArtifactView {
  id: string
  executionId: string
  kind: string
  name: string
  uri: string
  contentPreview: string
  validationStatus: string
  validationReason: string
  createdAt: string
}

export interface TaskDeliverableResultView {
  requirementId: string
  status: string
  artifactIds: string[]
  reason: string
}

export interface TaskCriterionResultView {
  criterionId: string
  status: string
  evidenceArtifactIds: string[]
  evidenceText: string
  reason: string
}

export interface TaskCompletionReportView {
  terminalStatus: string
  completionReason: string
  criterionResults: TaskCriterionResultView[]
  deliverableResults: TaskDeliverableResultView[]
  artifactIds: string[]
  humanAccepted: boolean
  awaitingHumanDecision?: boolean
  automaticGaps?: string[]
  decidedByType: string
  decidedById: string
  decidedAt: string
  evidenceSummary: string
}

export interface TaskExecutionHistoryItem {
  id: string
  attemptNo: number
  trigger: string
  triggerReason: string
  status: string
  reason: string
  actor: string
  actorId: string
  actorName: string
  time: {
    createdAt: string
    startedAt: string | null
    finishedAt: string | null
  }
  isActive: boolean
  report: TaskCompletionReportView | null
  artifacts: TaskArtifactView[]
}

type UnknownRecord = Record<string, unknown>

interface TaskArtifactSource extends UnknownRecord {
  id?: unknown
  execution_id?: unknown
  kind?: unknown
  name?: unknown
  content?: unknown
  uri?: unknown
  validation_status?: unknown
  validation_reason?: unknown
  created_at?: unknown
}

interface TaskDeliverableResultSource extends UnknownRecord {
  requirement_id?: unknown
  status?: unknown
  artifact_ids?: unknown
  reason?: unknown
}

interface TaskCompletionReportSource extends UnknownRecord {
  terminal_status?: unknown
  completion_reason?: unknown
  criterion_results?: unknown
  deliverable_results?: unknown
  artifact_ids?: unknown
  human_accepted?: unknown
  decided_by_type?: unknown
  decided_by_id?: unknown
  decided_at?: unknown
  evidence_summary?: unknown
}

interface TaskExecutionSource extends UnknownRecord {
  id?: unknown
  attempt_no?: unknown
  trigger_type?: unknown
  trigger_reason?: unknown
  triggered_by_user_id?: unknown
  triggered_by_user_name?: unknown
  status?: unknown
  created_at?: unknown
  started_at?: unknown
  finished_at?: unknown
  completion_report?: unknown
  artifacts?: unknown
  workflow_snapshot?: unknown
}

type TaskDetailSource = Task & {
  active_execution_id?: string | null
  artifacts?: TaskArtifactSource[]
  completion_report?: TaskCompletionReportSource | null
  contract?: {
    goal?: unknown
    deliverable_goal?: unknown
    deliverable_kind?: unknown
    deliverable_format?: unknown
    deliverable_filename?: unknown
    requires_human_acceptance?: unknown
  } | null
  draft?: (NonNullable<Task["draft"]> & {
    goal?: unknown
    deliverable_goal?: unknown
    deliverable_kind?: unknown
    deliverable_format?: unknown
    deliverable_filename?: unknown
  }) | null
  executions?: TaskExecutionSource[]
}

export function taskDetailSummaryBlocks(task: Task): TaskDetailSummaryBlock[] {
  return [
    {
      key: "request",
      title: "原始诉求",
      text: task.content || task.description || "-",
    },
    {
      key: "draft",
      title: "任务清单",
      text: draftTaskListText(task),
    },
  ]
}

export function taskFourQuestions(task: Task): TaskFourQuestion[] {
  const source = task as TaskDetailSource
  const goal = firstText(
    source.contract?.goal,
    source.draft?.goal,
    task.title,
    task.description,
    task.content,
  ) || "未记录任务目标"
  const completionReason = cleanText(source.completion_report?.completion_reason)
  const status = taskStatus(task)
  const completionText = isTaskAwaitingHumanAcceptance(task)
    ? "等待人工验收，任务尚未结束"
    : completionReason
    || (status === "running"
      ? "任务仍在运行，尚未结束"
      : cleanText(task.final_output)
        ? `未记录结束原因；最终输出：${cleanText(task.final_output)}`
        : `未记录结束原因；任务终态为 ${status}`)
  const deliverableText = taskDeliverableText(source)

  return [
    {
      key: "creator",
      title: "谁创建了它",
      text: firstText(task.created_by_user_name, task.created_by_user_id) || "未知",
    },
    { key: "goal", title: "目标是什么", text: goal },
    {
      key: "deliverable",
      title: "交付物是什么",
      text: deliverableText,
    },
    { key: "completion", title: "为什么可以结束", text: completionText },
  ]
}

function taskDeliverableText(source: TaskDetailSource) {
  const contractGoal = cleanText(source.contract?.deliverable_goal)
  const draftGoal = cleanText(source.draft?.deliverable_goal)
  const goal = contractGoal || draftGoal
  if (!goal) return "历史任务未单独记录交付物目标"

  const deliverySource = contractGoal ? source.contract : source.draft
  if (deliverySource?.deliverable_kind !== "file") return goal

  const format = deliverySource.deliverable_format === "text" ? "纯文本" : "Markdown"
  const filename = cleanText(deliverySource.deliverable_filename)
  const details = filename ? ["文件", format, filename] : ["文件", format]
  return `${goal}（${details.join(" / ")}）`
}

export function isTaskAwaitingHumanAcceptance(task: Task) {
  const source = task as TaskDetailSource
  return Boolean(
    taskStatus(task) === "running"
    && task.current_node === "human_intervention"
    && source.contract?.requires_human_acceptance
    && source.completion_report
    && !source.completion_report.human_accepted
  )
}

export function isTaskAwaitingHumanAdjudication(task: Task) {
  const source = task as TaskDetailSource
  return Boolean(
    taskStatus(task) === "running"
    && task.current_node === "human_intervention"
    && source.completion_report?.awaiting_human_decision
  )
}

export function taskInterventionView(task: Task) {
  if (isTaskAwaitingHumanAdjudication(task)) {
    return {
      awaitingAcceptance: false,
      awaitingAdjudication: true,
      title: "人工结果裁决",
      description: "自动验收无法确认任务是否成功，请结合执行结果和缺失证据判定成功或失败。",
      inputLabel: "裁决意见",
      placeholder: "填写判定依据、缺失内容或最终处理意见",
      submitText: "判定成功",
      requiresOutput: true,
    }
  }
  if (isTaskAwaitingHumanAcceptance(task)) {
    return {
      awaitingAcceptance: true,
      awaitingAdjudication: false,
      title: "人工验收",
      description: "自动检查已完成，等待人工确认交付结果后结束任务。",
      inputLabel: "验收说明（可选）",
      placeholder: "填写验收意见",
      submitText: "验收通过",
      requiresOutput: false,
    }
  }
  return {
    awaitingAcceptance: false,
    awaitingAdjudication: false,
    title: "人工介入处理",
    description: "流程当前无法自动继续。请补充最终处理结论。",
    inputLabel: "处理结论",
    placeholder: "填写最终结论、处理结果或后续说明",
    submitText: "完成任务",
    requiresOutput: true,
  }
}

export function taskHumanAcceptanceText(report: TaskCompletionReportView) {
  if (report.humanAccepted) return "已通过"
  return report.terminalStatus === "running" ? "待验收" : "未记录或无需验收"
}

export function buildTaskInterventionResultPayload(
  task: Task,
  output: string,
  decision: "succeeded" | "failed" = "succeeded",
) {
  const value = output.trim()
  if (isTaskAwaitingHumanAdjudication(task)) {
    return {
      result_status: decision,
      output: value,
      should_complete: true,
      metadata: {
        human_adjudicated: true,
        human_accepted: decision === "succeeded",
      },
    }
  }
  if (isTaskAwaitingHumanAcceptance(task)) {
    return {
      result_status: "succeeded" as const,
      output: value || "人工验收通过",
      should_complete: true,
      metadata: { human_accepted: true },
    }
  }
  return {
    result_status: "succeeded" as const,
    output: value,
    should_complete: true,
  }
}

export function taskArtifactViews(task: Task): TaskArtifactView[] {
  return artifactViews((task as TaskDetailSource).artifacts)
}

export function taskArtifactClickableUri(artifact: TaskArtifactView) {
  if (artifact.validationStatus !== "valid") return ""
  const uri = artifact.uri.trim()
  if (!uri) return ""
  try {
    const protocol = new URL(uri).protocol.toLowerCase()
    return protocol === "http:" || protocol === "https:" ? uri : ""
  } catch {
    return ""
  }
}

export function taskDeliverableResultViews(task: Task): TaskDeliverableResultView[] {
  return deliverableResultViews((task as TaskDetailSource).completion_report?.deliverable_results)
}

export function taskExecutionHistory(task: Task): TaskExecutionHistoryItem[] {
  const source = task as TaskDetailSource
  return (source.executions || [])
    .map((execution) => {
      const report = completionReportView(execution.completion_report)
      const triggerReason = cleanText(execution.trigger_reason)
      const actorId = cleanText(execution.triggered_by_user_id)
      const actorName = cleanText(execution.triggered_by_user_name)
      const status = cleanText(execution.status)
      return {
        id: cleanText(execution.id),
        attemptNo: numberValue(execution.attempt_no),
        trigger: cleanText(execution.trigger_type),
        triggerReason,
        status,
        reason: report?.completionReason || (status === "running" ? "" : triggerReason || status),
        actor: actorName || actorId || "系统",
        actorId,
        actorName,
        time: {
          createdAt: cleanText(execution.created_at),
          startedAt: nullableText(execution.started_at),
          finishedAt: nullableText(execution.finished_at),
        },
        isActive: cleanText(execution.id) === cleanText(source.active_execution_id),
        report,
        artifacts: artifactViews(execution.artifacts),
      }
    })
    .sort((left, right) => right.attemptNo - left.attemptNo)
}

export function executionHistoryActiveKeys(currentKeys: string[], activeExecutionId: string) {
  return activeExecutionId ? [activeExecutionId] : currentKeys
}

export function workflowDefinitionForTask(task: Task): WorkflowDefinition | undefined {
  const source = task as TaskDetailSource
  const activeExecution = (source.executions || []).find(
    (execution) => cleanText(execution.id) === cleanText(source.active_execution_id),
  )
  return workflowDefinition(activeExecution?.workflow_snapshot)
    || workflowDefinition(task.request_metadata?.workflow_definition)
}

export function taskDetailTypeBadge(task: Task) {
  return {
    text: taskTypeText(task),
    color: isManualWorkflowTask(task) ? "purple" : "blue",
  }
}

export function manualWorkflowFlowElements(
  task: Task,
  definition: WorkflowDefinition,
): { nodes: WorkflowReactFlowNode[]; edges: WorkflowReactFlowEdge[] } {
  const flow = workflowToReactFlow(definition, detailWorkflowNodePositions(definition))
  return {
    nodes: flow.nodes.map((node) => {
      const sourceNode = definition.nodes.find((item) => item.id === node.id)
      const subtask = workflowSubtaskForNode(task, node.id)
      const status = sourceNode ? workflowNodeState(task, sourceNode) : "pending"
      return {
        ...node,
        draggable: false,
        selectable: false,
        data: {
          ...node.data,
          status,
          statusText: workflowNodeStateText(status),
          kindText: workflowNodeKindText(sourceNode?.type),
          output: subtask?.output || "",
          assigneeUserName: subtask?.assignee_user_name || node.data.assigneeUserName || "",
        },
        className: `task-detail-flow-node ${status}`,
      }
    }),
    edges: flow.edges.map((edge) => ({
      ...edge,
      selectable: false,
      animated: false,
      style: {
        stroke: "#0f8ca8",
        strokeWidth: 2.2,
      },
    })),
  }
}

export function detailWorkflowNodePositions(definition: WorkflowDefinition) {
  return autoLayoutWorkflowNodePositions(definition, {
    top: 80,
    columnGap: 340,
    rowGap: 220,
  })
}

export function workflowNodeKindText(type?: string) {
  return { start: "开始", end: "完成", agent: "Agent", human: "人工", condition: "条件" }[type || ""] || type || "节点"
}

export function workflowNodeStateText(state: string) {
  return { pending: "未开始", running: "执行中", succeeded: "已完成", failed: "失败" }[state] || state
}

export function workflowNodeStateColor(state: string) {
  return { pending: "default", running: "processing", succeeded: "success", failed: "error" }[state] || "default"
}

export function workflowSubtaskForNode(task: Task, nodeId: string) {
  const subtasks = (task.context?.rounds || [])
    .flatMap((round) => round.subtasks || []) as Array<SubTask & { logical_key?: string }>
  const logicalMatch = subtasks.find((subtask) => cleanText(subtask.logical_key) === nodeId)
  if (logicalMatch) return logicalMatch

  const source = task as TaskDetailSource
  const acceptedIds = new Set([`${task.id}_${nodeId}`, nodeId])
  if (source.active_execution_id) {
    acceptedIds.add(`${task.id}_${source.active_execution_id}_${nodeId}`)
  }
  return subtasks.find((subtask) => acceptedIds.has(subtask.id))
}

export function compactContextText(value: unknown, maxLength = 96) {
  const text = displayContextValue(value).replace(/\s+/g, " ").trim()
  if (!text) return ""
  if (text.length <= maxLength) return text
  return `${text.slice(0, Math.max(0, maxLength - 3)).trim()}...`
}

export function taskContextNodeView(subtask: SubTask) {
  const output = String(subtask.output || "").trim()
  const description = String(subtask.description || "").trim()
  const error = String(subtask.error_message || "").trim()
  const preview = compactContextText(output || description || error || "暂无摘要", 86)
  return {
    title: subtask.title || subtask.id,
    typeText: subtask.assignee_type === "human" || subtask.current_node === "human"
      ? "人工"
      : subtask.assignee_type === "condition"
        ? "条件"
        : "Agent",
    assigneeText: subtask.assignee_user_name || subtask.assigned_agent_id || "",
    preview,
    hasDetail: Boolean(description || output || error || subtask.tool_results?.length),
  }
}

function displayContextValue(value: unknown) {
  if (value === undefined || value === null) return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function artifactViews(value: unknown): TaskArtifactView[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map((artifact) => ({
    id: cleanText(artifact.id),
    executionId: cleanText(artifact.execution_id),
    kind: cleanText(artifact.kind),
    name: cleanText(artifact.name) || cleanText(artifact.id),
    uri: cleanText(artifact.uri),
    contentPreview: compactContextText(artifact.content, 160),
    validationStatus: cleanText(artifact.validation_status),
    validationReason: cleanText(artifact.validation_reason),
    createdAt: cleanText(artifact.created_at),
  }))
}

function deliverableResultViews(value: unknown): TaskDeliverableResultView[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map((result) => ({
    requirementId: cleanText(result.requirement_id),
    status: cleanText(result.status),
    artifactIds: stringList(result.artifact_ids),
    reason: cleanText(result.reason),
  }))
}

function completionReportView(value: unknown): TaskCompletionReportView | null {
  if (!isRecord(value)) return null
  return {
    terminalStatus: cleanText(value.terminal_status),
    completionReason: cleanText(value.completion_reason),
    criterionResults: criterionResultViews(value.criterion_results),
    deliverableResults: deliverableResultViews(value.deliverable_results),
    artifactIds: stringList(value.artifact_ids),
    humanAccepted: value.human_accepted === true,
    awaitingHumanDecision: value.awaiting_human_decision === true,
    automaticGaps: stringList(value.automatic_gaps),
    decidedByType: cleanText(value.decided_by_type),
    decidedById: cleanText(value.decided_by_id),
    decidedAt: cleanText(value.decided_at),
    evidenceSummary: cleanText(value.evidence_summary),
  }
}

function criterionResultViews(value: unknown): TaskCriterionResultView[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map((result) => ({
    criterionId: cleanText(result.criterion_id),
    status: cleanText(result.status),
    evidenceArtifactIds: stringList(result.evidence_artifact_ids),
    evidenceText: cleanText(result.evidence_text),
    reason: cleanText(result.reason),
  }))
}

function workflowDefinition(value: unknown): WorkflowDefinition | undefined {
  if (!isRecord(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) return undefined
  return value as unknown as WorkflowDefinition
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = cleanText(value)
    if (text) return text
  }
  return ""
}

function cleanText(value: unknown) {
  if (value === undefined || value === null) return ""
  return String(value).trim()
}

function nullableText(value: unknown) {
  return cleanText(value) || null
}

function numberValue(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.map(cleanText).filter(Boolean) : []
}

function workflowNodeState(task: Task, node: WorkflowDefinition["nodes"][number]) {
  if (node.type === "start") return "succeeded"
  if (node.type === "end") return taskStatus(task) === "succeeded" ? "succeeded" : "pending"
  return workflowSubtaskForNode(task, node.id)?.status || "pending"
}

function taskStatus(task: Task) {
  return task.task_status || task.status || "running"
}

function draftTaskListText(task: Task) {
  if (!task.draft) return "暂无识别任务清单"
  const title = task.draft.title || "未命名任务"
  const description = task.draft.description || ""
  return description ? `${title}\n${description}` : title
}
