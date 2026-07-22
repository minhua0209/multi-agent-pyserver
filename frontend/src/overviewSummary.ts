import type { Task, TaskStatus } from "./api/taskhub"


export type OverviewRange = "all" | "today" | "7d"

export interface OverviewSummary {
  total: number
  today: number
  running: number
  succeeded: number
  failed: number
  blocked: number
  partial: number
  cancelled: number
  risk: number
  completionRate: number
}

export interface OverviewTrendPoint {
  key: string
  label: string
  value: number
}


function localDayStart(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

function localDateKey(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, "0")
  const day = String(value.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function validDate(value?: string) {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function overviewTaskStatus(task: Task): TaskStatus {
  return task.task_status || task.status || "running"
}

export function isWithinOverviewRange(value: string | undefined, range: OverviewRange, now = new Date()) {
  if (range === "all") return true
  const parsed = validDate(value)
  if (!parsed) return false

  const todayStart = localDayStart(now)
  const tomorrowStart = new Date(todayStart)
  tomorrowStart.setDate(tomorrowStart.getDate() + 1)
  if (range === "today") return parsed >= todayStart && parsed < tomorrowStart

  const sevenDayStart = new Date(todayStart)
  sevenDayStart.setDate(sevenDayStart.getDate() - 6)
  return parsed >= sevenDayStart && parsed < tomorrowStart
}

export function filterOverviewTasks(tasks: Task[], range: OverviewRange, now = new Date()) {
  if (range === "all") return tasks.slice()
  return tasks.filter((task) => isWithinOverviewRange(task.created_at, range, now))
}

export function buildOverviewSummary(tasks: Task[], now = new Date()): OverviewSummary {
  const counts: Record<TaskStatus, number> = {
    running: 0,
    succeeded: 0,
    failed: 0,
    blocked: 0,
    partial: 0,
    cancelled: 0,
  }

  tasks.forEach((task) => {
    counts[overviewTaskStatus(task)] += 1
  })

  const total = tasks.length
  const risk = counts.failed + counts.blocked + counts.partial
  return {
    total,
    today: tasks.filter((task) => isWithinOverviewRange(task.created_at, "today", now)).length,
    ...counts,
    risk,
    completionRate: total ? Math.round((counts.succeeded / total) * 100) : 0,
  }
}

export function buildOverviewTrend(tasks: Task[], now = new Date()): OverviewTrendPoint[] {
  const todayStart = localDayStart(now)
  const points = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(todayStart)
    date.setDate(date.getDate() - (6 - index))
    return {
      key: localDateKey(date),
      label: `${date.getMonth() + 1}/${date.getDate()}`,
      value: 0,
    }
  })
  const pointsByKey = new Map(points.map((point) => [point.key, point]))

  tasks.forEach((task) => {
    const createdAt = validDate(task.created_at)
    if (!createdAt) return
    const point = pointsByKey.get(localDateKey(createdAt))
    if (point) point.value += 1
  })

  return points
}

export function overviewRiskTasks(tasks: Task[], limit = 4) {
  const severity: Partial<Record<TaskStatus, number>> = { failed: 3, blocked: 2, partial: 1 }
  return tasks
    .filter((task) => severity[overviewTaskStatus(task)])
    .sort((left, right) => {
      const statusDifference = (severity[overviewTaskStatus(right)] || 0) - (severity[overviewTaskStatus(left)] || 0)
      if (statusDifference) return statusDifference
      const rightTime = validDate(right.updated_at || right.created_at)?.getTime() || 0
      const leftTime = validDate(left.updated_at || left.created_at)?.getTime() || 0
      return rightTime - leftTime
    })
    .slice(0, Math.max(0, limit))
}

export function overviewRecentEvents<T>(events: readonly T[], limit = 6) {
  return events.slice(0, Math.max(0, limit))
}
