import { afterEach, describe, expect, it, vi } from "vitest"

import {
  buildTaskRequestPayload,
  listAssignableUsers,
  listTasks,
  setCurrentUserId,
  WorkflowTaskMetadata,
} from "./taskhub"

function mockJsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("taskhub api client", () => {
  afterEach(() => {
    setCurrentUserId("")
    vi.unstubAllGlobals()
  })

  it("sends current user id header with api requests", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse([]))
    vi.stubGlobal("fetch", fetchMock)

    setCurrentUserId("user_001")
    await listTasks()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/tasks",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-User-Id": "user_001",
        }),
      }),
    )
  })

  it("loads assignable users for workflow human node selection", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse([{ id: "user_001", name: "张三", role: "user" }]))
    vi.stubGlobal("fetch", fetchMock)

    const users = await listAssignableUsers()

    expect(users).toEqual([{ id: "user_001", name: "张三", role: "user" }])
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/users/assignable", expect.any(Object))
  })

  it("keeps workflow definition in task request metadata", () => {
    const payload = buildTaskRequestPayload("客户交付任务", "处理客户需求", {
      execution_mode: "workflow_template",
      workflow_name: "客户交付任务",
      workflow_definition: {
        nodes: [
          {
            id: "review",
            type: "human",
            config: {
              assignee_user_id: "user_001",
              assignee_user_name: "张三",
              assignee_role: "user",
            },
          },
        ],
        edges: [],
      },
    })

    const metadata = payload.metadata as WorkflowTaskMetadata
    expect(metadata.workflow_definition?.nodes[0].config).toMatchObject({
      assignee_user_id: "user_001",
      assignee_user_name: "张三",
      assignee_role: "user",
    })
  })
})
