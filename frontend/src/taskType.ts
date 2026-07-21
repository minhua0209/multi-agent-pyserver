import { Task, TaskType } from "./api/taskhub"

type TaskTypeSource = Pick<Task, "task_type" | "request_metadata"> & { id?: string }
type TaskNodeSource = TaskTypeSource & Pick<Task, "current_node" | "task_status" | "status">

const TASK_NODE_LABELS: Record<string, string> = {
  intent_recognition: "任务识别",
  human_confirmation: "任务清单确认",
  waiting_dependencies: "等待前置任务",
  dispatch_decision: "智能分发",
  subtask_execution: "子任务执行",
  context_update: "上下文更新",
  agent_execution: "Agent 执行",
  human_execution: "人工处理",
  completion_judge: "完成判断",
  human_intervention: "人工介入",
}

export function taskType(task: TaskTypeSource): TaskType {
  if (task.task_type) return task.task_type
  if (task.request_metadata?.execution_mode === "workflow_template") return "manual_orchestration"
  return "auto_planning"
}

export function taskTypeText(task: TaskTypeSource) {
  return taskType(task) === "manual_orchestration" ? "手动编排" : "自动规划"
}

export function isManualWorkflowTask(task: TaskTypeSource) {
  return taskType(task) === "manual_orchestration"
}

export function taskNodeText(task: TaskNodeSource) {
  const currentNode = task.current_node || ""
  if (!currentNode) return "-"

  const status = task.task_status || task.status
  if (status === "succeeded" && currentNode === "completion_judge") return "已完成"
  if (status === "running" && currentNode === "human_confirmation") return "待确认任务清单"
  if (status === "running" && currentNode === "human_execution") return "等待人工处理"
  if (status === "running" && isManualWorkflowTask(task) && currentNode === "dispatch_decision") return "流程执行中"

  return TASK_NODE_LABELS[currentNode] || currentNode
}
