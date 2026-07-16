import { Task, TaskType } from "./api/taskhub"

type TaskTypeSource = Pick<Task, "task_type" | "request_metadata"> & { id?: string }

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
