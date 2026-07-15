import type { Task } from "./api/taskhub"

export function taskLabel() {
  return "任务"
}

export function draftTitleValue(task: Pick<Task, "id" | "title" | "content" | "draft">) {
  return task.title || task.content || task.id
}

export function draftDescriptionValue(task: Pick<Task, "description" | "content" | "draft">) {
  return task.draft?.description || task.description || task.content || ""
}
