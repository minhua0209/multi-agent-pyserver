import { describe, expect, it } from "vitest"

import {
  applyNodeConfig,
  applyNodeInstruction,
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
      "condition_content",
    ])
    expect(workflowNodeInlineEditFields(result.nodes[2].data)).toEqual([])
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
})
