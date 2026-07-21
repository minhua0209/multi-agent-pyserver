import { describe, expect, it } from "vitest"

import type { Task } from "./api/taskhub"
import {
  buildOverviewSummary,
  buildOverviewTrend,
  filterOverviewTasks,
  isWithinOverviewRange,
  overviewRecentEvents,
  overviewRiskTasks,
} from "./overviewSummary"


const now = new Date(2026, 6, 21, 15, 30, 0)

function task(id: string, status: Task["status"], createdAt: Date | string): Task {
  return {
    id,
    title: `任务 ${id}`,
    status,
    created_at: typeof createdAt === "string" ? createdAt : createdAt.toISOString(),
  }
}


describe("overview task range", () => {
  it("filters by local day and the latest seven local calendar days", () => {
    const tasks = [
      task("today", "running", new Date(2026, 6, 21, 0, 0, 0)),
      task("six-days-ago", "succeeded", new Date(2026, 6, 15, 0, 0, 0)),
      task("seven-days-ago", "failed", new Date(2026, 6, 14, 23, 59, 59)),
      task("invalid", "blocked", "not-a-date"),
    ]

    expect(filterOverviewTasks(tasks, "today", now).map((item) => item.id)).toEqual(["today"])
    expect(filterOverviewTasks(tasks, "7d", now).map((item) => item.id)).toEqual([
      "today",
      "six-days-ago",
    ])
    expect(filterOverviewTasks(tasks, "all", now)).toHaveLength(4)
  })

  it("can apply the same range rule to human subtask timestamps", () => {
    expect(isWithinOverviewRange(new Date(2026, 6, 21, 9, 0, 0).toISOString(), "today", now)).toBe(true)
    expect(isWithinOverviewRange(new Date(2026, 6, 20, 23, 59, 59).toISOString(), "today", now)).toBe(false)
    expect(isWithinOverviewRange(undefined, "7d", now)).toBe(false)
  })
})


describe("overview summary", () => {
  it("counts every status and excludes cancelled tasks from risk", () => {
    const tasks = [
      task("running", "running", now),
      task("succeeded", "succeeded", now),
      task("failed", "failed", now),
      task("blocked", "blocked", now),
      task("partial", "partial", now),
      task("cancelled", "cancelled", now),
    ]

    expect(buildOverviewSummary(tasks, now)).toEqual({
      total: 6,
      today: 6,
      running: 1,
      succeeded: 1,
      failed: 1,
      blocked: 1,
      partial: 1,
      cancelled: 1,
      risk: 3,
      completionRate: 17,
    })
  })

  it("returns a stable empty summary", () => {
    expect(buildOverviewSummary([], now)).toEqual({
      total: 0,
      today: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      blocked: 0,
      partial: 0,
      cancelled: 0,
      risk: 0,
      completionRate: 0,
    })
  })
})


describe("overview detail collections", () => {
  it("builds a seven-day trend and ignores invalid timestamps", () => {
    const tasks = [
      task("day-one-a", "running", new Date(2026, 6, 15, 10, 0, 0)),
      task("day-one-b", "succeeded", new Date(2026, 6, 15, 18, 0, 0)),
      task("today", "failed", new Date(2026, 6, 21, 8, 0, 0)),
      task("invalid", "partial", "not-a-date"),
    ]

    const trend = buildOverviewTrend(tasks, now)
    expect(trend).toHaveLength(7)
    expect(trend[0]).toMatchObject({ key: "2026-07-15", value: 2 })
    expect(trend[6]).toMatchObject({ key: "2026-07-21", value: 1 })
  })

  it("orders risk tasks by severity and then recency", () => {
    const tasks = [
      task("partial", "partial", new Date(2026, 6, 21, 12, 0, 0)),
      task("blocked", "blocked", new Date(2026, 6, 21, 11, 0, 0)),
      task("failed-old", "failed", new Date(2026, 6, 20, 12, 0, 0)),
      task("failed-new", "failed", new Date(2026, 6, 21, 10, 0, 0)),
      task("running", "running", new Date(2026, 6, 21, 13, 0, 0)),
    ]

    expect(overviewRiskTasks(tasks, 4).map((item) => item.id)).toEqual([
      "failed-new",
      "failed-old",
      "blocked",
      "partial",
    ])
  })

  it("keeps recent events independent from the task creation range", () => {
    const events = [
      { id: "today-on-old-task", task_id: "old-task" },
      { id: "older-on-new-task", task_id: "new-task" },
      { id: "third", task_id: "another-task" },
    ]

    expect(overviewRecentEvents(events, 2)).toEqual(events.slice(0, 2))
  })
})
