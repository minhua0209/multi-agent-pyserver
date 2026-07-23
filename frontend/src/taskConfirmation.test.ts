import { describe, expect, it, vi } from "vitest"

import type { Task } from "./api/taskhub"
import {
  buildTaskConfirmationRequests,
  buildTaskConfirmPayload,
  cancelTasksSequentially,
  confirmTaskRequestsSequentially,
  confirmationDraftFromTask,
  confirmationTaskIdsToCancelOnClose,
  isTaskAwaitingConfirmation,
  validateConfirmationDraft,
} from "./taskConfirmation"


describe("task confirmation draft", () => {
  it("uses visible task draft suggestions while normalizing hidden contract fields", () => {
    const task = {
      id: "task_1",
      title: "Fallback title",
      content: "Fallback request",
      draft: {
        title: "  交付方案  ",
        description: "  输出可评审方案  ",
        goal: "  完成技术方案  ",
        deliverable_goal: "  一份可评审文档  ",
        deliverable_kind: "file",
        deliverable_format: "markdown",
        deliverable_filename: "  implementation-plan.MD  ",
        deliverable_requirements: ["  包含架构图  ", "", "   ", "包含风险清单"],
        success_criteria: ["  评审通过  ", "\n", "关键风险有应对措施"],
        requires_human_acceptance: true,
      },
    } as unknown as Task

    expect(confirmationDraftFromTask(task)).toEqual({
      title: "Fallback title",
      description: "输出可评审方案",
      goal: "完成技术方案",
      deliverableGoal: "一份可评审文档",
      successCriteria: ["包含架构图", "包含风险清单", "评审通过", "关键风险有应对措施"],
    })
  })

  it("keeps the submitted task title instead of replacing it with the recognized checklist title", () => {
    const task = {
      id: "task_weather",
      title: "最近7天天气报告",
      content: "查询最近7天的天气情况，最后把调查结果写入文档",
      draft: {
        title: "查询最近7天的天气情况; 将调查结果写入文档",
        description: "- 查询最近7天的天气情况: 获取天气数据。\n- 将调查结果写入文档: 写入本地 reports 目录。",
      },
    } as unknown as Task

    const draft = confirmationDraftFromTask(task)

    expect(draft.title).toBe("最近7天天气报告")
    expect(draft.description).toContain("将调查结果写入文档")
  })

  it("merges legacy criteria while ignoring hidden incident draft fields", () => {
    const draft = confirmationDraftFromTask({
      id: "task_incident",
      title: "bug修复3",
      draft: {
        goal: "修复问题",
        deliverable_goal: "提交修复结果",
        deliverable_kind: "file",
        deliverable_format: "text",
        deliverable_filename: "bug1_fix_code.patch",
        deliverable_requirements: ["输出根因分析", "提交修复代码"],
        success_criteria: ["提交修复代码", "测试通过"],
        requires_human_acceptance: true,
      },
    } as unknown as Task)

    expect(draft).toEqual({
      title: "bug修复3",
      description: "bug修复3",
      goal: "修复问题",
      deliverableGoal: "提交修复结果",
      successCriteria: ["输出根因分析", "提交修复代码", "测试通过"],
    })
    expect(validateConfirmationDraft(draft)).toEqual([])
  })

  it("builds editable defaults for manual or legacy tasks without suggestions", () => {
    const task = {
      id: "task_manual",
      title: "客户交付流程",
      content: "完成客户资料审核并归档",
      task_type: "manual_orchestration",
    } as Task

    const draft = confirmationDraftFromTask(task)

    expect(draft.title).toBe("客户交付流程")
    expect(draft.description).toBe("完成客户资料审核并归档")
    expect(draft.goal).toBe("完成客户资料审核并归档")
    expect(draft.deliverableGoal).toContain("客户交付流程")
    expect(draft.successCriteria).toHaveLength(1)
    expect(draft.successCriteria[0]).toContain("完成客户资料审核并归档")
    expect(validateConfirmationDraft(draft)).toEqual([])
  })

  it("requires goal, deliverable goal and at least one success criterion", () => {
    expect(validateConfirmationDraft({
      title: "任务",
      description: "描述",
      goal: "  ",
      deliverableGoal: "",
      successCriteria: [" ", "\n"],
    })).toEqual([
      "请填写任务目标",
      "请填写交付物目标",
      "请至少填写一条验收标准",
    ])
  })

  it("limits manually edited acceptance criteria to ten entries", () => {
    const draft = confirmationDraftFromTask({
      id: "task_many_criteria",
      title: "多验收标准任务",
      draft: {
        success_criteria: Array.from({ length: 12 }, (_, index) => `标准 ${index + 1}`),
      },
    } as unknown as Task)

    expect(draft.successCriteria).toHaveLength(10)
    expect(validateConfirmationDraft({
      ...draft,
      successCriteria: Array.from({ length: 11 }, (_, index) => `人工标准 ${index + 1}`),
    })).toContain("验收标准最多填写 10 条")
    expect(buildTaskConfirmPayload({
      ...draft,
      successCriteria: Array.from({ length: 11 }, (_, index) => `人工标准 ${index + 1}`),
    }).contract.success_criteria).toHaveLength(10)
  })

  it("deduplicates criteria case-insensitively before applying the ten item limit", () => {
    const boundaryCriteria = [
      "Criterion 1",
      "criterion 1",
      ...Array.from({ length: 8 }, (_, index) => `标准 ${index + 2}`),
    ]
    const draft = confirmationDraftFromTask({
      id: "task_casefold_boundary",
      title: "验收标准边界",
      draft: {
        deliverable_requirements: boundaryCriteria,
        success_criteria: ["CRITERION 1", "最后真实标准"],
      },
    } as unknown as Task)
    const expected = [
      "Criterion 1",
      ...Array.from({ length: 8 }, (_, index) => `标准 ${index + 2}`),
      "最后真实标准",
    ]

    expect(draft.successCriteria).toEqual(expected)
    expect(buildTaskConfirmPayload(draft).contract.success_criteria.map((item) => item.description)).toEqual(expected)
  })

  it("builds a confirmation contract from visible fields only", () => {
    const payload = buildTaskConfirmPayload(
      {
        ...confirmationDraftFromTask({ id: "task_payload" } as Task),
        title: "  发布方案  ",
        description: "  输出实施方案  ",
        goal: "  完成发布设计  ",
        deliverableGoal: "  可执行方案  ",
        successCriteria: [
          "  包含回滚步骤  ",
          "包含负责人",
          "  评审通过  ",
          "",
          "可按步骤执行",
        ],
      },
      {
        execution_mode: "async",
        default_assignee_user_id: "user_1",
        default_assignee_user_name: "李晨",
        default_assignee_role: "user",
      },
    )

    expect(payload).toEqual({
      title: "发布方案",
      description: "输出实施方案",
      execution_mode: "async",
      default_assignee_user_id: "user_1",
      default_assignee_user_name: "李晨",
      default_assignee_role: "user",
      contract: {
        goal: "完成发布设计",
        deliverable_goal: "可执行方案",
        success_criteria: [
          { id: "", description: "包含回滚步骤" },
          { id: "", description: "包含负责人" },
          { id: "", description: "评审通过" },
          { id: "", description: "可按步骤执行" },
        ],
      },
    })
  })

  it("only exposes continuation for running tasks at human confirmation", () => {
    expect(isTaskAwaitingConfirmation({
      id: "task_pending",
      task_status: "running",
      current_node: "human_confirmation",
    } as Task)).toBe(true)
    expect(isTaskAwaitingConfirmation({
      id: "task_dispatching",
      task_status: "running",
      current_node: "dispatch_decision",
    } as Task)).toBe(false)
    expect(isTaskAwaitingConfirmation({
      id: "task_cancelled",
      task_status: "cancelled",
      current_node: "human_confirmation",
    } as Task)).toBe(false)
  })

  it("builds recovered confirmation requests with the shared draft payload", () => {
    const task = {
      id: "task_recovered",
      title: "恢复确认任务",
      description: "确认恢复后的任务契约",
      task_status: "running",
      current_node: "human_confirmation",
    } as Task
    const draft = {
      ...confirmationDraftFromTask(task),
      goal: "  恢复并执行任务  ",
      deliverableGoal: "  可评审的恢复结果  ",
      successCriteria: ["  包含恢复说明  ", "  可以继续执行  "],
    }

    expect(buildTaskConfirmationRequests(
      [task],
      { [task.id]: draft },
      { execution_mode: "async" },
    )).toEqual([
      {
        taskId: task.id,
        payload: {
          title: "恢复确认任务",
          description: "确认恢复后的任务契约",
          execution_mode: "async",
          contract: {
            goal: "恢复并执行任务",
            deliverable_goal: "可评审的恢复结果",
            success_criteria: [
              { id: "", description: "包含恢复说明" },
              { id: "", description: "可以继续执行" },
            ],
          },
        },
      },
    ])
  })

  it("reports each confirmed task before stopping at the first unconfirmed failure", async () => {
    const requests = ["task_1", "task_2", "task_3"].map((taskId) => ({
      taskId,
      payload: buildTaskConfirmPayload({
        ...confirmationDraftFromTask({ id: taskId } as Task),
        title: taskId,
        description: `${taskId} description`,
        goal: `${taskId} goal`,
        deliverableGoal: `${taskId} deliverable`,
        successCriteria: [`${taskId} succeeds`],
      }),
    }))
    const confirm = vi.fn(async (taskId: string) => {
      if (taskId === "task_2") throw new Error("confirm failed")
      return { id: taskId, task_status: "running", current_node: "dispatch_decision", contract: {} } as Task
    })
    const reconcile = vi.fn(async (taskId: string) => ({
      id: taskId,
      task_status: "running",
      current_node: "human_confirmation",
      contract: null,
    } as Task))
    const confirmedIds: string[] = []

    await expect(confirmTaskRequestsSequentially(
      requests,
      confirm,
      reconcile,
      (task) => {
        confirmedIds.push(task.id)
      },
    )).rejects.toThrow("confirm failed")

    expect(confirmedIds).toEqual(["task_1"])
    expect(confirm.mock.calls.map(([taskId]) => taskId)).toEqual(["task_1", "task_2"])
    expect(reconcile).toHaveBeenCalledWith("task_2")
  })

  it("reconciles an uncertain confirmation response and continues the batch", async () => {
    const requests = ["task_1", "task_2"].map((taskId) => ({
      taskId,
      payload: buildTaskConfirmPayload({
        ...confirmationDraftFromTask({ id: taskId } as Task),
        title: taskId,
        description: `${taskId} description`,
        goal: `${taskId} goal`,
        deliverableGoal: `${taskId} deliverable`,
        successCriteria: [`${taskId} succeeds`],
      }),
    }))
    const confirm = vi.fn(async (taskId: string) => {
      if (taskId === "task_1") throw new Error("network response lost")
      return { id: taskId, task_status: "running", current_node: "dispatch_decision", contract: {} } as Task
    })
    const reconcile = vi.fn(async (taskId: string) => ({
      id: taskId,
      task_status: "running",
      current_node: "dispatch_decision",
      contract: { goal: "confirmed" },
    } as unknown as Task))
    const confirmedIds: string[] = []

    await confirmTaskRequestsSequentially(
      requests,
      confirm,
      reconcile,
      (task) => {
        confirmedIds.push(task.id)
      },
    )

    expect(confirmedIds).toEqual(["task_1", "task_2"])
    expect(reconcile).toHaveBeenCalledWith("task_1")
  })

  it("reports each cancelled task before stopping at the first cancellation failure", async () => {
    const cancel = vi.fn(async (taskId: string) => {
      if (taskId === "task_2") throw new Error("cancel failed")
    })
    const reconcile = vi.fn(async (taskId: string) => ({
      id: taskId,
      task_status: "running",
      current_node: "human_confirmation",
    } as Task))
    const cancelledIds: string[] = []

    await expect(cancelTasksSequentially(
      ["task_1", "task_2", "task_3"],
      cancel,
      reconcile,
      (taskId) => {
        cancelledIds.push(taskId)
      },
    )).rejects.toThrow("cancel failed")

    expect(cancelledIds).toEqual(["task_1"])
    expect(cancel.mock.calls.map(([taskId]) => taskId)).toEqual(["task_1", "task_2"])
    expect(reconcile).toHaveBeenCalledWith("task_2")
  })

  it("reconciles an uncertain cancellation response and continues the batch", async () => {
    const cancel = vi.fn(async (taskId: string) => {
      if (taskId === "task_1") throw new Error("network response lost")
    })
    const reconcile = vi.fn(async (taskId: string) => ({
      id: taskId,
      task_status: "cancelled",
      current_node: "completion_judge",
    } as Task))
    const cancelledIds: string[] = []

    await cancelTasksSequentially(
      ["task_1", "task_2"],
      cancel,
      reconcile,
      (taskId) => {
        cancelledIds.push(taskId)
      },
    )

    expect(cancelledIds).toEqual(["task_1", "task_2"])
    expect(reconcile).toHaveBeenCalledWith("task_1")
  })

  it("does not cancel task rows when a continuation confirmation modal closes", () => {
    expect(confirmationTaskIdsToCancelOnClose(
      [
        { id: "task_1", task_status: "running", current_node: "human_confirmation" } as Task,
      ],
      ["task_1"],
      false,
    )).toEqual([])
  })
})
