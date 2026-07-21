import { describe, expect, it } from "vitest"

import { Task, WorkflowDefinition } from "./api/taskhub"
import {
  compactContextText,
  buildTaskInterventionResultPayload,
  executionHistoryActiveKeys,
  manualWorkflowFlowElements,
  taskArtifactClickableUri,
  taskArtifactViews,
  taskContextNodeView,
  taskDeliverableResultViews,
  taskDetailSummaryBlocks,
  taskDetailTypeBadge,
  taskExecutionHistory,
  taskFourQuestions,
  taskHumanAcceptanceText,
  taskInterventionView,
  workflowDefinitionForTask,
  workflowSubtaskForNode,
} from "./taskDetailView"

describe("task detail view helpers", () => {
  const definition: WorkflowDefinition = {
    nodes: [
      { id: "start", type: "start", title: "开始", description: "初始化上下文" },
      { id: "agent_1", type: "agent", title: "需求分析 Agent", description: "梳理需求" },
      { id: "end", type: "end", title: "完成", description: "汇总输出" },
    ],
    edges: [
      { from: "start", to: "agent_1", condition: {} },
      { from: "agent_1", to: "end", condition: {} },
    ],
  }

  const task: Task = {
    id: "task_1",
    title: "客户需求协同处理",
    content: "请分析客户需求",
    task_type: "manual_orchestration",
    task_status: "succeeded",
    request_metadata: {
      workflow_name: "客户需求协同处理",
      workflow_definition: definition,
    },
    draft: {
      title: "客户需求协同处理",
      description: "请分析客户需求",
    },
    context: {
      rounds: [
        {
          id: "round_1",
          round_index: 1,
          execution_mode: "workflow_template",
          subtasks: [
            {
              id: "task_1_agent_1",
              title: "需求分析 Agent",
              status: "succeeded",
              output: "已识别需求范围。",
            },
          ],
        },
      ],
    },
  }

  it("keeps task type as a compact badge instead of a summary block", () => {
    expect(taskDetailSummaryBlocks(task).map((block) => block.title)).toEqual(["原始诉求", "任务清单"])
    expect(taskDetailTypeBadge(task)).toEqual({ text: "手动编排", color: "purple" })
  })

  it("maps manual workflow definition to readonly React Flow elements with node state", () => {
    const result = manualWorkflowFlowElements(task, definition)

    expect(result.nodes.map((node) => node.id)).toEqual(["start", "agent_1", "end"])
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["start", "agent_1"],
      ["agent_1", "end"],
    ])
    expect(result.nodes.find((node) => node.id === "agent_1")?.data).toMatchObject({
      status: "succeeded",
      statusText: "已完成",
      output: "已识别需求范围。",
    })
    expect(result.nodes.find((node) => node.id === "end")?.data).toMatchObject({
      status: "succeeded",
      statusText: "已完成",
    })
  })

  it("lays out long manual workflows with clear rows instead of squeezing every node into one line", () => {
    const longDefinition: WorkflowDefinition = {
      nodes: [
        { id: "start", type: "start", title: "开始" },
        { id: "agent_1", type: "agent", title: "需求分析 Agent" },
        { id: "agent_2", type: "agent", title: "技术方案 Agent" },
        { id: "agent_3", type: "agent", title: "接口设计 Agent" },
        { id: "agent_4", type: "agent", title: "后端研发 Agent" },
        { id: "agent_5", type: "agent", title: "代码评审 Agent" },
        { id: "end", type: "end", title: "完成" },
      ],
      edges: [
        { from: "start", to: "agent_1", condition: {} },
        { from: "agent_1", to: "agent_2", condition: {} },
        { from: "agent_2", to: "agent_3", condition: {} },
        { from: "agent_3", to: "agent_4", condition: {} },
        { from: "agent_4", to: "agent_5", condition: {} },
        { from: "agent_5", to: "end", condition: {} },
      ],
    }

    const result = manualWorkflowFlowElements({ ...task, task_status: "running" }, longDefinition)
    const positions = Object.fromEntries(result.nodes.map((node) => [node.id, node.position]))

    expect(result.nodes[0].style).toMatchObject({ width: 260 })
    expect(positions.start).toEqual({ x: 80, y: 80 })
    expect(positions.agent_1).toEqual({ x: 420, y: 80 })
    expect(positions.agent_2).toEqual({ x: 760, y: 80 })
    expect(positions.agent_3).toEqual({ x: 1100, y: 80 })
    expect(positions.agent_4).toEqual({ x: 1440, y: 80 })
    expect(positions.agent_5).toEqual({ x: 80, y: 480 })
    expect(positions.end).toEqual({ x: 420, y: 480 })
  })

  it("keeps condition branches in the next workflow column on task detail graphs", () => {
    const branchingDefinition: WorkflowDefinition = {
      nodes: [
        { id: "start", type: "start", title: "开始" },
        { id: "condition_1", type: "condition", title: "条件判断" },
        { id: "human_large_order", type: "human", title: "大额订单人工确认" },
        { id: "human_small_order", type: "human", title: "小额订单人工确认" },
        { id: "end", type: "end", title: "完成" },
      ],
      edges: [
        { from: "start", to: "condition_1", condition: {} },
        { from: "condition_1", to: "human_large_order", condition: { type: "decision", value: "v1" } },
        { from: "condition_1", to: "human_small_order", condition: { type: "decision", value: "v2" } },
        { from: "human_large_order", to: "end", condition: {} },
        { from: "human_small_order", to: "end", condition: {} },
      ],
    }

    const result = manualWorkflowFlowElements({ ...task, task_status: "running" }, branchingDefinition)
    const positions = Object.fromEntries(result.nodes.map((node) => [node.id, node.position]))

    expect(positions.human_large_order.x).toBeGreaterThan(positions.condition_1.x)
    expect(positions.human_small_order.x).toBe(positions.human_large_order.x)
    expect(positions.end.x).toBeGreaterThan(positions.human_large_order.x)
    expect(Math.abs(positions.human_small_order.y - positions.human_large_order.y)).toBeGreaterThanOrEqual(210)
  })

  it("builds compact node context previews for collapsible detail cards", () => {
    expect(compactContextText("第一行\n第二行   很长很长很长", 10)).toBe("第一行 第二行...")
    expect(
      taskContextNodeView({
        id: "subtask_1",
        title: "需求分析 Agent",
        assignee_type: "agent",
        assigned_agent_id: "agent_requirement",
        status: "succeeded",
        description: "梳理业务诉求、目标用户和边界。",
        output: "结论：需求缺少业务背景，需要补充客户画像、业务流程和验收标准。",
      }),
    ).toMatchObject({
      title: "需求分析 Agent",
      typeText: "Agent",
      assigneeText: "agent_requirement",
      preview: "结论：需求缺少业务背景，需要补充客户画像、业务流程和验收标准。",
      hasDetail: true,
    })
    expect(
      taskContextNodeView({
        id: "human_1",
        title: "",
        assignee_type: "human",
        assignee_user_name: "李晨",
        status: "running",
        description: "请人工确认折扣是否通过。",
      }),
    ).toMatchObject({
      title: "human_1",
      typeText: "人工",
      assigneeText: "李晨",
      preview: "请人工确认折扣是否通过。",
    })
  })

  it("answers the four task questions from the confirmed contract and completion report", () => {
    const result = taskFourQuestions({
      ...task,
      created_by_user_id: "user_1",
      created_by_user_name: "张三",
      contract: {
        goal: "形成可执行的客户需求方案",
        deliverable_goal: "一份可评审的需求方案",
      },
      draft: {
        ...task.draft,
        goal: "草稿目标",
        deliverable_goal: "草稿交付物",
      },
      completion_report: {
        completion_reason: "全部成功标准均已满足",
      },
      final_output: "不应覆盖完成报告",
    } as Task)

    expect(result).toEqual([
      { key: "creator", title: "谁创建了它", text: "张三" },
      { key: "goal", title: "目标是什么", text: "形成可执行的客户需求方案" },
      { key: "deliverable", title: "交付物是什么", text: "一份可评审的需求方案" },
      { key: "completion", title: "为什么可以结束", text: "全部成功标准均已满足" },
    ])
  })

  it("uses explicit legacy and running fallbacks for the four task questions", () => {
    const runningLegacyTask = {
      id: "task_legacy_running",
      title: "历史任务标题",
      content: "原始内容",
      created_by_user_id: "legacy_user",
      task_status: "running",
    } as Task

    expect(taskFourQuestions(runningLegacyTask)).toEqual([
      { key: "creator", title: "谁创建了它", text: "legacy_user" },
      { key: "goal", title: "目标是什么", text: "历史任务标题" },
      { key: "deliverable", title: "交付物是什么", text: "历史任务未单独记录交付物目标" },
      { key: "completion", title: "为什么可以结束", text: "任务仍在运行，尚未结束" },
    ])

    const terminalWithOutput = {
      id: "task_legacy_failed",
      content: "修复失败任务",
      task_status: "failed",
      final_output: "依赖服务不可用",
    } as Task
    expect(taskFourQuestions(terminalWithOutput).map((item) => item.text)).toEqual([
      "未知",
      "修复失败任务",
      "历史任务未单独记录交付物目标",
      "未记录结束原因；最终输出：依赖服务不可用",
    ])

    const terminalWithoutOutput = {
      id: "task_legacy_cancelled",
      content: "已取消任务",
      task_status: "cancelled",
    } as Task
    expect(taskFourQuestions(terminalWithoutOutput)[3].text).toBe("未记录结束原因；任务终态为 cancelled")
  })

  it("keeps required human acceptance explicitly unfinished and builds acceptance payload", () => {
    const pendingAcceptance = {
      ...task,
      task_status: "running",
      current_node: "human_intervention",
      contract: {
        goal: "完成交付",
        deliverable_goal: "可验收交付物",
        requires_human_acceptance: true,
      },
      completion_report: {
        terminal_status: "running",
        completion_reason: "全部自动检查已通过",
        human_accepted: false,
      },
      final_output: "原始交付内容",
    } as Task

    expect(taskFourQuestions(pendingAcceptance)[3].text).toBe("等待人工验收，任务尚未结束")
    expect(taskInterventionView(pendingAcceptance)).toMatchObject({
      awaitingAcceptance: true,
      title: "人工验收",
      submitText: "验收通过",
      requiresOutput: false,
    })
    expect(buildTaskInterventionResultPayload(pendingAcceptance, "")).toEqual({
      result_status: "succeeded",
      output: "人工验收通过",
      should_complete: true,
      metadata: { human_accepted: true },
    })
  })

  it("builds explicit success and failure decisions for automatic completion gaps", () => {
    const pendingAdjudication = {
      ...task,
      task_status: "running",
      current_node: "human_intervention",
      completion_report: {
        terminal_status: "running",
        completion_reason: "Awaiting human adjudication",
        human_accepted: false,
        awaiting_human_decision: true,
        automatic_gaps: ["criterion has no passed evidence"],
      },
      final_output: "已有执行结果",
    } as Task

    expect(taskInterventionView(pendingAdjudication)).toMatchObject({
      awaitingAdjudication: true,
      title: "人工结果裁决",
      submitText: "判定成功",
      requiresOutput: true,
    })
    expect(buildTaskInterventionResultPayload(pendingAdjudication, "证据足够", "succeeded")).toEqual({
      result_status: "succeeded",
      output: "证据足够",
      should_complete: true,
      metadata: { human_adjudicated: true, human_accepted: true },
    })
    expect(buildTaskInterventionResultPayload(pendingAdjudication, "证据不足", "failed")).toEqual({
      result_status: "failed",
      output: "证据不足",
      should_complete: true,
      metadata: { human_adjudicated: true, human_accepted: false },
    })
  })

  it("maps only output artifacts, deliverable results and execution history for direct UI use", () => {
    const taskWithHistory = {
      ...task,
      active_execution_id: "execution_2",
      request_metadata: {
        attachments: [{ id: "attachment_input", filename: "输入需求.docx" }],
      },
      context: {
        ...task.context,
        artifacts: ["context_only_artifact"],
      },
      artifacts: [
        {
          id: "artifact_current",
          execution_id: "execution_2",
          kind: "file",
          name: "需求方案.pdf",
          content: "",
          uri: "/outputs/需求方案.pdf",
          validation_status: "valid",
          validation_reason: "文件可读取",
          created_at: "2026-07-20T10:06:00Z",
        },
        {
          id: "artifact_summary",
          execution_id: "execution_2",
          kind: "text",
          name: "方案摘要",
          content: "这是可直接展示的方案摘要。",
          uri: "",
          validation_status: "pending",
          validation_reason: "等待人工确认",
          created_at: "2026-07-20T10:07:00Z",
        },
      ],
      completion_report: {
        completion_reason: "交付物已通过验证",
        deliverable_results: [
          {
            requirement_id: "requirement_pdf",
            status: "passed",
            artifact_ids: ["artifact_current"],
            reason: "PDF 已生成并校验",
          },
        ],
      },
      executions: [
        {
          id: "execution_2",
          attempt_no: 2,
          trigger_type: "rerun",
          trigger_reason: "补齐 PDF 交付物",
          triggered_by_user_id: "user_2",
          triggered_by_user_name: "",
          status: "running",
          created_at: "2026-07-20T10:05:00Z",
          started_at: "2026-07-20T10:05:01Z",
          finished_at: null,
          completion_report: null,
          artifacts: [
            {
              id: "artifact_current",
              execution_id: "execution_2",
              kind: "file",
              name: "需求方案.pdf",
              content: "",
              uri: "/outputs/需求方案.pdf",
              validation_status: "valid",
              validation_reason: "文件可读取",
              created_at: "2026-07-20T10:06:00Z",
            },
          ],
        },
        {
          id: "execution_1",
          attempt_no: 1,
          trigger_type: "initial",
          trigger_reason: "首次执行",
          triggered_by_user_id: "user_1",
          triggered_by_user_name: "张三",
          status: "failed",
          created_at: "2026-07-20T09:00:00Z",
          started_at: "2026-07-20T09:00:01Z",
          finished_at: "2026-07-20T09:03:00Z",
          completion_report: {
            completion_reason: "缺少 PDF 交付物",
            terminal_status: "failed",
            criterion_results: [
              {
                criterion_id: "criterion_pdf",
                status: "failed",
                evidence_artifact_ids: ["artifact_missing"],
                evidence_text: "未找到 PDF",
                reason: "PDF 交付物缺失",
              },
            ],
            deliverable_results: [],
            artifact_ids: [],
            human_accepted: false,
            decided_by_type: "system",
            decided_by_id: "task_graph",
            decided_at: "2026-07-20T09:03:00Z",
            evidence_summary: "0/1 success criteria passed",
          },
          artifacts: [],
        },
      ],
    } as Task

    expect(taskArtifactViews(taskWithHistory)).toEqual([
      {
        id: "artifact_current",
        executionId: "execution_2",
        kind: "file",
        name: "需求方案.pdf",
        uri: "/outputs/需求方案.pdf",
        contentPreview: "",
        validationStatus: "valid",
        validationReason: "文件可读取",
        createdAt: "2026-07-20T10:06:00Z",
      },
      {
        id: "artifact_summary",
        executionId: "execution_2",
        kind: "text",
        name: "方案摘要",
        uri: "",
        contentPreview: "这是可直接展示的方案摘要。",
        validationStatus: "pending",
        validationReason: "等待人工确认",
        createdAt: "2026-07-20T10:07:00Z",
      },
    ])
    expect(taskArtifactViews(taskWithHistory).map((item) => item.id)).not.toContain("attachment_input")
    expect(taskArtifactViews(taskWithHistory).map((item) => item.id)).not.toContain("context_only_artifact")

    expect(taskDeliverableResultViews(taskWithHistory)).toEqual([
      {
        requirementId: "requirement_pdf",
        status: "passed",
        artifactIds: ["artifact_current"],
        reason: "PDF 已生成并校验",
      },
    ])

    const history = taskExecutionHistory(taskWithHistory)
    expect(history.map((item) => item.id)).toEqual(["execution_2", "execution_1"])
    expect(history[0]).toMatchObject({
      attemptNo: 2,
      trigger: "rerun",
      triggerReason: "补齐 PDF 交付物",
      status: "running",
      reason: "",
      actor: "user_2",
      isActive: true,
      report: null,
      time: {
        createdAt: "2026-07-20T10:05:00Z",
        startedAt: "2026-07-20T10:05:01Z",
        finishedAt: null,
      },
    })
    expect(history[0].artifacts.map((item) => item.id)).toEqual(["artifact_current"])
    expect(history[1]).toMatchObject({
      attemptNo: 1,
      trigger: "initial",
      status: "failed",
      reason: "缺少 PDF 交付物",
      actor: "张三",
      isActive: false,
      report: {
        completionReason: "缺少 PDF 交付物",
        terminalStatus: "failed",
        criterionResults: [
          {
            criterionId: "criterion_pdf",
            status: "failed",
            evidenceArtifactIds: ["artifact_missing"],
            evidenceText: "未找到 PDF",
            reason: "PDF 交付物缺失",
          },
        ],
        humanAccepted: false,
        decidedByType: "system",
        decidedById: "task_graph",
        decidedAt: "2026-07-20T09:03:00Z",
      },
    })
  })

  it.each([
    ["valid https", "valid", "https://example.com/report.pdf", "https://example.com/report.pdf"],
    ["valid http", "valid", "http://example.com/report.pdf", "http://example.com/report.pdf"],
    ["file URI", "valid", "file:///tmp/report.pdf", ""],
    ["data URI", "valid", "data:text/html,unsafe", ""],
    ["javascript URI", "valid", "javascript:alert(1)", ""],
    ["custom protocol", "valid", "taskhub://artifact/1", ""],
    ["relative URI", "valid", "/outputs/report.pdf", ""],
    ["invalid artifact", "invalid", "https://example.com/report.pdf", ""],
    ["pending artifact", "pending", "https://example.com/report.pdf", ""],
  ])("only exposes clickable links for %s", (_, validationStatus, uri, expected) => {
    expect(taskArtifactClickableUri({
      id: "artifact_1",
      executionId: "execution_1",
      kind: "file",
      name: "report.pdf",
      uri,
      contentPreview: "",
      validationStatus,
      validationReason: "",
      createdAt: "",
    })).toBe(expected)
  })

  it("moves the controlled execution history expansion to the new active execution", () => {
    expect(executionHistoryActiveKeys(["execution_1"], "execution_2")).toEqual(["execution_2"])
    expect(executionHistoryActiveKeys(["execution_1"], "")).toEqual(["execution_1"])
  })

  it("describes human acceptance without treating an optional missing record as a failure", () => {
    const report = {
      terminalStatus: "succeeded",
      completionReason: "完成",
      criterionResults: [],
      deliverableResults: [],
      artifactIds: [],
      humanAccepted: false,
      decidedByType: "system",
      decidedById: "task_graph",
      decidedAt: "2026-07-20T10:00:00Z",
      evidenceSummary: "",
    }

    expect(taskHumanAcceptanceText(report)).toBe("未记录或无需验收")
    expect(taskHumanAcceptanceText({ ...report, terminalStatus: "running" })).toBe("待验收")
    expect(taskHumanAcceptanceText({ ...report, humanAccepted: true })).toBe("已通过")
  })

  it("prefers the active execution workflow snapshot and falls back to request metadata", () => {
    const requestDefinition: WorkflowDefinition = {
      nodes: [{ id: "legacy_node", type: "human", title: "旧节点" }],
      edges: [],
    }
    const snapshotDefinition: WorkflowDefinition = {
      nodes: [{ id: "snapshot_node", type: "agent", title: "快照节点" }],
      edges: [],
    }
    const taskWithSnapshot = {
      ...task,
      active_execution_id: "execution_2",
      request_metadata: { workflow_definition: requestDefinition },
      executions: [
        { id: "execution_1", workflow_snapshot: requestDefinition },
        { id: "execution_2", workflow_snapshot: snapshotDefinition },
      ],
    } as unknown as Task

    expect(workflowDefinitionForTask(taskWithSnapshot)).toEqual(snapshotDefinition)
    expect(workflowDefinitionForTask({ ...taskWithSnapshot, active_execution_id: "missing" } as Task)).toEqual(
      requestDefinition,
    )
  })

  it("matches workflow subtasks by logical key before strict legacy and execution-scoped IDs", () => {
    const scopedTask = {
      ...task,
      active_execution_id: "execution_2",
      context: {
        rounds: [
          {
            subtasks: [
              {
                id: "task_1_other_node",
                execution_id: "execution_2",
                logical_key: "agent_1",
                title: "逻辑键优先",
              },
              {
                id: "task_1_execution_2_scoped_node",
                execution_id: "execution_2",
                title: "执行范围 ID",
              },
              { id: "task_1_legacy_node", title: "任务前缀旧 ID" },
              { id: "bare_node", title: "裸节点旧 ID" },
              { id: "task_1_execution_2_peer_review", title: "其它节点" },
            ],
          },
        ],
      },
    } as Task

    expect(workflowSubtaskForNode(scopedTask, "agent_1")?.title).toBe("逻辑键优先")
    expect(workflowSubtaskForNode(scopedTask, "scoped_node")?.title).toBe("执行范围 ID")
    expect(workflowSubtaskForNode(scopedTask, "legacy_node")?.title).toBe("任务前缀旧 ID")
    expect(workflowSubtaskForNode(scopedTask, "bare_node")?.title).toBe("裸节点旧 ID")
    expect(workflowSubtaskForNode(scopedTask, "review")).toBeUndefined()
  })
})
