import { afterEach, describe, expect, it, vi } from "vitest"

import {
  buildTaskRequestPayload,
  confirmTask,
  createTaskRerun,
  getTaskExecution,
  listAssignableUsers,
  listTaskExecutions,
  listTasks,
  preflightTaskRerun,
  submitTaskResult,
  setCurrentUserId,
  uploadTaskAttachment,
  type Artifact,
  type CompletionReport,
  type DeliverableResult,
  type RerunIssue,
  type RerunSideEffect,
  type SubTask,
  type Task,
  type TaskContract,
  type TaskContractItem,
  type TaskExecution,
  type TaskRerunCreate,
  type TaskRerunPreflightRequest,
  type TaskRerunPreflightResponse,
  type TaskRerunResponse,
  type TaskStatus,
  WorkflowTaskMetadata,
} from "./taskhub"

function mockJsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
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

  it("submits task-level intervention result", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ id: "task_001", task_status: "succeeded" }))
    vi.stubGlobal("fetch", fetchMock)

    await submitTaskResult("task_001", {
      result_status: "succeeded",
      output: "人工补充最终结论",
      should_complete: true,
    })

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/tasks/task_001/result", expect.any(Object))
    expect(JSON.parse(String(options.body))).toEqual({
      result_status: "succeeded",
      output: "人工补充最终结论",
      should_complete: true,
    })
  })

  it("keeps task contract, execution and rerun response fields typed", () => {
    const status: TaskStatus = "cancelled"
    const contractItem = { id: "criterion_001", description: "结果必须可验收" } satisfies TaskContractItem
    const contract = {
      goal: "完成任务",
      deliverable_goal: "交付验收报告",
      deliverable_requirements: [contractItem],
      success_criteria: [contractItem],
      requires_human_acceptance: true,
      version: 1,
      confirmed_by_user_id: "user_001",
      confirmed_by_user_name: "张三",
      confirmed_at: "2026-07-20T00:00:00Z",
      legacy_inferred: false,
    } satisfies TaskContract
    const artifact = {
      id: "artifact_001",
      task_id: "task_001",
      execution_id: "execution_001",
      kind: "text",
      source_type: "task_result",
      source_id: "task_001",
      name: "验收报告",
      content: "完成",
      uri: "",
      media_type: "text/plain",
      checksum: "",
      validation_status: "valid",
      validation_reason: "",
      deliverable_requirement_ids: ["criterion_001"],
      source_artifact_id: null,
      reused_from_execution_id: null,
      metadata: {},
      created_at: "2026-07-20T00:00:00Z",
    } satisfies Artifact
    const deliverableResult = {
      requirement_id: "criterion_001",
      status: "passed",
      artifact_ids: [artifact.id],
      reason: "已交付",
    } satisfies DeliverableResult
    const completionReport = {
      id: "report_001",
      execution_id: "execution_001",
      terminal_status: "succeeded",
      completion_reason: "验收通过",
      criterion_results: [],
      deliverable_results: [deliverableResult],
      artifact_ids: [artifact.id],
      workflow_end_node_id: null,
      human_accepted: true,
      decided_by_type: "human",
      decided_by_id: "user_001",
      decided_at: "2026-07-20T00:00:00Z",
      evidence_summary: "报告完整",
    } satisfies CompletionReport
    const subtask = {
      id: "execution_001:review",
      execution_id: "execution_001",
      logical_key: "review",
    } satisfies SubTask
    const execution = {
      id: "execution_001",
      task_id: "task_001",
      attempt_no: 1,
      trigger_type: "initial",
      trigger_reason: "",
      triggered_by_user_id: "user_001",
      triggered_by_user_name: "张三",
      contract_snapshot: contract,
      workflow_snapshot: null,
      status,
      start_node: "dispatch_decision",
      current_node: "completion_judge",
      context_snapshot: { summary: "", rounds: [], artifacts: [] },
      artifacts: [artifact],
      loop_count: 1,
      final_output: "完成",
      created_at: "2026-07-20T00:00:00Z",
      started_at: "2026-07-20T00:00:00Z",
      finished_at: "2026-07-20T00:01:00Z",
      parent_execution_id: null,
      retry_of_execution_id: null,
      idempotency_key: "rerun-001",
      request_fingerprint: "fingerprint",
      execution_mode: "sync",
      side_effects_confirmed_by_user_id: "",
      side_effects_confirmed_by_user_name: "",
      side_effects_confirmed_at: null,
      completion_report: completionReport,
    } satisfies TaskExecution
    const issue = { code: "task_not_terminal", message: "任务尚未结束" } satisfies RerunIssue
    const sideEffect = {
      subtask_id: subtask.id,
      tool_execution_id: "tool_execution_001",
      tool_name: "send_message",
      tool_type: "http",
      argument_keys: ["recipient"],
      success: true,
    } satisfies RerunSideEffect
    const preflightRequest = { source_execution_id: execution.id } satisfies TaskRerunPreflightRequest
    const createRequest = {
      ...preflightRequest,
      reason: "修复失败结果",
      execution_mode: "async",
      confirm_side_effects: true,
    } satisfies TaskRerunCreate
    const preflight = {
      task_id: "task_001",
      source_execution_id: execution.id,
      next_attempt_no: 2,
      dependencies_satisfied: true,
      start_node: "dispatch_decision",
      will_wait_for_dependencies: false,
      allowed: false,
      issues: [issue],
      side_effects: [sideEffect],
      requires_side_effect_confirmation: true,
    } satisfies TaskRerunPreflightResponse
    const task = {
      id: "task_001",
      draft: {
        title: "任务草稿",
        description: "描述",
        goal: "完成任务",
        deliverable_goal: "交付报告",
        deliverable_requirements: ["报告"],
        success_criteria: ["报告可验收"],
        requires_human_acceptance: true,
      },
      contract,
      executions: [execution],
      active_execution_id: execution.id,
      artifacts: [artifact],
      completion_report: completionReport,
    } satisfies Task
    const rerun = {
      task,
      execution,
      replayed: false,
      scheduled: true,
      execution_is_active: true,
    } satisfies TaskRerunResponse

    expect(createRequest.source_execution_id).toBe("execution_001")
    expect(preflight.side_effects[0].argument_keys).toEqual(["recipient"])
    expect(rerun.task.active_execution_id).toBe("execution_001")
  })

  it("lists and gets encoded task execution resources", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mockJsonResponse([]))
      .mockResolvedValueOnce(mockJsonResponse({ id: "execution/001" }))
    vi.stubGlobal("fetch", fetchMock)

    await listTaskExecutions("task/001")
    await getTaskExecution("task/001", "execution/001")

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/tasks/task%2F001/executions", expect.any(Object))
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/tasks/task%2F001/executions/execution%2F001", expect.any(Object))
  })

  it("posts rerun preflight payload", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ allowed: true }))
    vi.stubGlobal("fetch", fetchMock)

    await preflightTaskRerun("task_001", { source_execution_id: "execution_001" })

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/tasks/task_001/executions/preflight", expect.any(Object))
    expect(options.method).toBe("POST")
    expect(JSON.parse(String(options.body))).toEqual({ source_execution_id: "execution_001" })
  })

  it("creates rerun with idempotency key header", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ replayed: false }, 201))
    vi.stubGlobal("fetch", fetchMock)
    const payload = {
      source_execution_id: "execution_001",
      reason: "重新执行失败任务",
      execution_mode: "async" as const,
      confirm_side_effects: true,
    }

    await createTaskRerun("task/001", payload, "rerun-key-001")

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/tasks/task%2F001/executions", expect.any(Object))
    expect(options.method).toBe("POST")
    expect(options.headers).toMatchObject({ "Idempotency-Key": "rerun-key-001" })
    expect(JSON.parse(String(options.body))).toEqual(payload)
  })

  it("sends task contract when confirming a task", async () => {
    const fetchMock = vi.fn(async () => mockJsonResponse({ id: "task_001" }))
    vi.stubGlobal("fetch", fetchMock)
    const contract = {
      goal: "解决客户问题",
      deliverable_goal: "交付问题分析报告",
      deliverable_requirements: [{ id: "deliverable_001", description: "包含根因和修复建议" }],
      success_criteria: [{ id: "criterion_001", description: "报告通过人工验收" }],
      requires_human_acceptance: true,
    }

    await confirmTask("task_001", { title: "问题分析", description: "完成问题分析", contract })

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(options.body)).contract).toEqual(contract)
  })

  it.each([428, 409])("formats %s object detail as readable error text", async (status) => {
    const fetchMock = vi.fn(async () => mockJsonResponse({
      detail: {
        allowed: false,
        issues: [{ code: "side_effect_confirmation_required", message: "需要确认外部副作用" }],
      },
    }, status))
    vi.stubGlobal("fetch", fetchMock)

    const request = createTaskRerun("task_001", {
      source_execution_id: "execution_001",
      reason: "重新执行",
    }, "rerun-key-001")

    await expect(request).rejects.toThrow("需要确认外部副作用")
    await expect(request).rejects.not.toThrow("[object Object]")
  })

  it("falls back to HTTP status and body when an error response is not JSON", async () => {
    const fetchMock = vi.fn(async () => new Response("upstream timeout", {
      status: 502,
      statusText: "Bad Gateway",
      headers: { "Content-Type": "text/plain" },
    }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(listTasks()).rejects.toThrow(
      "接口请求失败：502 Bad Gateway；upstream timeout",
    )
  })
})
