import { afterEach, describe, expect, it, vi } from "vitest"

import {
  buildTaskRequestPayload,
  confirmTask,
  listAssignableUsers,
  listTasks,
  setCurrentUserId,
  uploadTaskAttachment,
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

  it("uploads text attachment with multipart form data", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ id: "att_001", filename: "需求.md" }))
    vi.stubGlobal("fetch", fetchMock)

    await uploadTaskAttachment(new File(["需求内容"], "需求.md", { type: "text/markdown" }))

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/task-attachments", expect.any(Object))
    expect(options.body).toBeInstanceOf(FormData)
    expect(options.headers).not.toHaveProperty("Content-Type")
  })

  it("sends default human assignee when confirming a task", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ id: "task_001" }))
    vi.stubGlobal("fetch", fetchMock)

    await confirmTask("task_001", {
      title: "确认研发方案",
      description: "需要人工确认研发方案是否通过",
      execution_mode: "async",
      default_assignee_user_id: "user_001",
      default_assignee_user_name: "李晨",
      default_assignee_role: "user",
    })

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/tasks/task_001/confirm", expect.any(Object))
    expect(JSON.parse(String(options.body))).toMatchObject({
      default_assignee_user_id: "user_001",
      default_assignee_user_name: "李晨",
      default_assignee_role: "user",
    })
  })
})
