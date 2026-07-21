import { describe, expect, it } from "vitest"

import { canNavigateToPage, refreshTargetsForPage } from "./pageRefresh"

describe("page refresh policy", () => {
  it("refreshes task data when entering task list", () => {
    expect(refreshTargetsForPage("tasks", true)).toEqual(["tasks"])
  })

  it("refreshes human subtasks when entering human confirmation workbench", () => {
    expect(refreshTargetsForPage("confirmation", true)).toEqual(["humanSubtasks"])
  })

  it("refreshes workflow node data and assignable users when entering node management", () => {
    expect(refreshTargetsForPage("agents", true)).toEqual(["agents", "assignableUsers"])
  })

  it("refreshes users and assignable users when entering user management", () => {
    expect(refreshTargetsForPage("users", true)).toEqual(["users", "assignableUsers"])
  })

  it("blocks non-admin users from admin-only pages", () => {
    expect(canNavigateToPage("agents", false)).toBe(false)
    expect(refreshTargetsForPage("agents", false)).toEqual([])
  })
})
