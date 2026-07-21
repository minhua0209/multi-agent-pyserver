import { describe, expect, it } from "vitest"

import {
  applyNodeConfig,
  applyNodeInstruction,
  canEditDecisionEdge,
  normalizeWorkflowConditionOptions,
  setDecisionEdgeCondition,
  workflowConditionDecisionValues,
  workflowNodeDetailItems,
  workflowNodeInlineEditFields,
  reduceWorkflowInlineTextDraft,
  workflowToReactFlow,
} from "./workflowReactFlow"

describe("workflowReactFlow helpers", () => {
  it("maps workflow nodes and edges to React Flow elements", () => {
    const result = workflowToReactFlow(
      {
        nodes: [
          { id: "start", type: "start", title: "开始" },
          { id: "agent_1", type: "agent", title: "合同 Agent", agent_id: "agent_contract" },
          { id: "end", type: "end", title: "完成" },
        ],
        edges: [
          { from: "start", to: "agent_1", condition: {} },
          { from: "agent_1", to: "end", condition: {} },
        ],
      },
      {
        start: { left: 0, top: 120 },
        agent_1: { left: 260, top: 120 },
        end: { left: 520, top: 120 },
      },
    )

    expect(result.nodes[1]).toMatchObject({
      id: "agent_1",
      type: "workflowNode",
      position: { x: 260, y: 120 },
      data: { title: "合同 Agent", kind: "agent", agentId: "agent_contract" },
    })
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["start", "agent_1"],
      ["agent_1", "end"],
    ])
  })

  it("stores execution instructions on an agent node config", () => {
    const definition = applyNodeInstruction(
      {
        nodes: [
          { id: "start", type: "start" },
          { id: "agent_1", type: "agent", title: "合同 Agent", config: { context_inputs: ["task.content"] } },
        ],
        edges: [{ from: "start", to: "agent_1", condition: {} }],
      },
      "agent_1",
      "重点检查合同风险，输出风险等级。",
    )

    expect(definition.nodes.find((node) => node.id === "agent_1")?.config).toMatchObject({
      context_inputs: ["task.content"],
      execution_instruction: "重点检查合同风险，输出风险等级。",
    })
  })

  it("stores editable human and condition node config", () => {
    const definition = applyNodeConfig(
      {
        nodes: [
          { id: "review", type: "human", title: "人工确认", config: { required_metadata: ["decision"] } },
          { id: "judge", type: "condition", title: "条件判断", config: { field: "decision" } },
        ],
        edges: [],
      },
      "review",
      {
        assignee_user_id: "user_001",
        assignee_user_name: "张三",
        assignee_role: "user",
        handoff_instruction: "请重点确认风险说明。",
      },
    )

    expect(definition.nodes.find((node) => node.id === "review")?.config).toMatchObject({
      required_metadata: ["decision"],
      assignee_user_id: "user_001",
      assignee_user_name: "张三",
      assignee_role: "user",
      handoff_instruction: "请重点确认风险说明。",
    })

    const updated = applyNodeConfig(definition, "judge", {
      condition_description: "人工通过后完成，否则返工",
      condition_content: "decision=approved -> 完成；decision=rejected -> 返工",
    })
    expect(updated.nodes.find((node) => node.id === "judge")?.config).toMatchObject({
      field: "decision",
      condition_description: "人工通过后完成，否则返工",
      condition_content: "decision=approved -> 完成；decision=rejected -> 返工",
    })
  })

  it("builds compact hover detail items with agent names instead of ids", () => {
    expect(
      workflowNodeDetailItems({
        id: "agent_1",
        title: "合同 Agent",
        description: "检查合同风险并输出建议。",
        kind: "agent",
        agentId: "agent_contract",
        agentName: "合同审核 Agent",
        instruction: "重点检查违约条款。",
      }),
    ).toEqual([
      { label: "类型", value: "Agent" },
      { label: "节点", value: "agent_1" },
      { label: "描述", value: "检查合同风险并输出建议。" },
      { label: "Agent", value: "合同审核 Agent" },
      { label: "交代", value: "重点检查违约条款。" },
    ])
  })

  it("maps human and condition config into hover detail items", () => {
    const result = workflowToReactFlow({
      nodes: [
        {
          id: "review",
          type: "human",
          title: "人工确认",
          config: {
            assignee_user_id: "user_001",
            assignee_user_name: "张三",
            assignee_role: "user",
            handoff_instruction: "请确认是否通过。",
          },
        },
        {
          id: "judge",
          type: "condition",
          title: "条件判断",
          config: {
            condition_description: "按人工确认结果判断",
            condition_content: "approved 完成；rejected 返工",
          },
        },
      ],
      edges: [],
    })

    expect(workflowNodeDetailItems(result.nodes[0].data)).toEqual([
      { label: "类型", value: "人工" },
      { label: "节点", value: "review" },
      { label: "人员", value: "张三" },
      { label: "角色", value: "user" },
      { label: "交代", value: "请确认是否通过。" },
    ])
    expect(workflowNodeDetailItems(result.nodes[1].data)).toContainEqual({
      label: "条件",
      value: "按人工确认结果判断",
    })
    expect(workflowNodeDetailItems(result.nodes[1].data)).toContainEqual({
      label: "内容",
      value: "approved 完成；rejected 返工",
    })
  })

  it("exposes inline editable fields for human and condition canvas nodes", () => {
    const result = workflowToReactFlow({
      nodes: [
        {
          id: "review",
          type: "human",
          title: "人工确认",
          config: {
            assignee_user_id: "user_002",
            assignee_user_name: "李四",
            assignee_role: "user",
            handoff_instruction: "确认金额和风险。",
          },
        },
        {
          id: "judge",
          type: "condition",
          title: "条件判断",
          config: {
            condition_description: "按审批结果判断",
            condition_content: "approved -> end",
          },
        },
        { id: "agent_1", type: "agent", title: "文档检查" },
      ],
      edges: [],
    })

    expect(workflowNodeInlineEditFields(result.nodes[0].data)).toEqual([
      {
        key: "assignee_user_id",
        label: "指定人员",
        inputType: "user_select",
        value: "user_002",
        placeholder: "请选择人员姓名",
      },
      {
        key: "handoff_instruction",
        label: "人工交代",
        inputType: "textarea",
        value: "确认金额和风险。",
        placeholder: "给人工确认人的处理要求、注意事项或输出格式",
      },
    ])
    expect(workflowNodeInlineEditFields(result.nodes[1].data).map((field) => field.key)).toEqual([
      "condition_description",
      "condition_options",
    ])
    expect(workflowNodeInlineEditFields(result.nodes[1].data).find((field) => field.key === "condition_options")).toMatchObject({
      conditionOptions: [],
    })
    expect(workflowNodeInlineEditFields(result.nodes[2].data)).toEqual([])
  })

  it("maps condition options into editable fields and edge decision values", () => {
    const result = workflowToReactFlow({
      nodes: [
        {
          id: "judge",
          type: "condition",
          title: "条件判断",
          config: {
            condition_description: "判断测试结论",
            condition_options: [
              { value: "approved", content: "测试通过、可以继续上线" },
              { value: "rejected", content: "测试不通过、需要返工" },
            ],
          },
        },
      ],
      edges: [],
    })
    const fields = workflowNodeInlineEditFields(result.nodes[0].data)

    expect(fields.find((field) => field.key === "condition_options")).toMatchObject({
      inputType: "condition_options",
      conditionOptions: [
        { value: "approved", content: "测试通过、可以继续上线" },
        { value: "rejected", content: "测试不通过、需要返工" },
      ],
    })
    expect(workflowNodeDetailItems(result.nodes[0].data)).toContainEqual({
      label: "条件项",
      value: "approved：测试通过、可以继续上线；rejected：测试不通过、需要返工",
    })
    expect(
      workflowConditionDecisionValues({
        id: "judge",
        type: "condition",
        config: {
          condition_options: [
            { value: "approved", content: "测试通过、可以继续上线" },
            { value: "rejected", content: "测试不通过、需要返工" },
          ],
          allowed_decisions: ["legacy"],
        },
      }),
    ).toEqual(["approved", "rejected"])
  })

  it("does not prefill condition options when a condition node has no branch config", () => {
    const node = { id: "judge", type: "condition", title: "条件判断", config: {} }
    const result = workflowToReactFlow({ nodes: [node], edges: [] })

    expect(workflowNodeInlineEditFields(result.nodes[0].data).find((field) => field.key === "condition_options")).toMatchObject({
      conditionOptions: [],
    })
    expect(workflowConditionDecisionValues(node)).toEqual([])
  })

  it("preserves empty condition option rows for canvas editing", () => {
    expect(
      normalizeWorkflowConditionOptions([
        { value: "", content: "" },
        { value: "", content: "" },
      ]),
    ).toEqual([
      { value: "", content: "" },
      { value: "", content: "" },
    ])

    const result = workflowToReactFlow({
      nodes: [
        {
          id: "judge",
          type: "condition",
          title: "条件判断",
          config: {
            condition_options: [
              { value: "", content: "" },
              { value: "", content: "" },
            ],
          },
        },
      ],
      edges: [],
    })

    expect(workflowNodeInlineEditFields(result.nodes[0].data).find((field) => field.key === "condition_options")).toMatchObject({
      conditionOptions: [
        { value: "", content: "" },
        { value: "", content: "" },
      ],
    })
  })

  it("buffers inline textarea changes while Chinese IME composition is active", () => {
    let result = reduceWorkflowInlineTextDraft({ value: "", composing: false }, { type: "composition_start" })
    expect(result).toEqual({
      state: { value: "", composing: true },
      commitValue: undefined,
    })

    result = reduceWorkflowInlineTextDraft(result.state, {
      type: "change",
      value: "qing",
      isComposing: true,
    })
    expect(result).toEqual({
      state: { value: "qing", composing: true },
      commitValue: undefined,
    })

    result = reduceWorkflowInlineTextDraft(result.state, {
      type: "composition_end",
      value: "请确认合同风险",
    })
    expect(result).toEqual({
      state: { value: "请确认合同风险", composing: false },
      commitValue: "请确认合同风险",
    })
  })

  it("edits decision conditions only for condition node outgoing edges", () => {
    const nodes = [
      { id: "judge", type: "condition", title: "条件判断" },
      { id: "release", type: "agent", title: "上线" },
      { id: "start", type: "start", title: "开始" },
    ]
    const edge = {
      id: "judge-release",
      source: "judge",
      target: "release",
      data: { condition: {} },
    }

    expect(canEditDecisionEdge(nodes, edge)).toBe(true)
    expect(canEditDecisionEdge(nodes, { ...edge, source: "start" })).toBe(false)
    expect(setDecisionEdgeCondition(edge, "approved")).toMatchObject({
      data: { condition: { type: "decision", value: "approved" } },
      label: "approved",
      animated: true,
    })
  })

  it("auto lays out branching templates with readable spacing and no node overlap", () => {
    const result = workflowToReactFlow(
      {
        nodes: [
          { id: "start", type: "start", title: "开始" },
          {
            id: "condition_1",
            type: "condition",
            title: "条件判断",
            config: {
              condition_options: [
                { value: "v1", content: "订单价格大于或等于1000块" },
                { value: "v2", content: "订单价格少于1000块" },
              ],
            },
          },
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
      },
      {},
    )

    const nodes = Object.fromEntries(result.nodes.map((node) => [node.id, node]))
    expect(nodes.condition_1.position.x).toBeGreaterThan(nodes.start.position.x)
    expect(nodes.human_large_order.position.x).toBeGreaterThan(nodes.condition_1.position.x)
    expect(nodes.human_small_order.position.x).toBe(nodes.human_large_order.position.x)
    expect(nodes.end.position.x).toBeGreaterThan(nodes.human_large_order.position.x)
    expect(Math.abs(nodes.human_small_order.position.y - nodes.human_large_order.position.y)).toBeGreaterThanOrEqual(210)

    const rectangles = result.nodes.map((node) => ({
      id: node.id,
      left: node.position.x,
      top: node.position.y,
      right: node.position.x + Number(node.style?.width || 260),
      bottom: node.position.y + (node.data.kind === "condition" ? 140 : 150),
    }))
    for (let index = 0; index < rectangles.length; index += 1) {
      for (let nextIndex = index + 1; nextIndex < rectangles.length; nextIndex += 1) {
        const first = rectangles[index]
        const second = rectangles[nextIndex]
        expect(
          first.right <= second.left ||
          second.right <= first.left ||
          first.bottom <= second.top ||
          second.bottom <= first.top,
          `${first.id} should not overlap ${second.id}`,
        ).toBe(true)
      }
    }
  })
})
