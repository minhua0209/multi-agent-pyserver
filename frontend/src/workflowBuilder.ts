import { Agent, WorkflowDefinition, WorkflowNode } from "./api/taskhub"

export type AgentSummary = Pick<Agent, "id" | "name" | "description" | "capabilities">

const fallbackParallelAgents: AgentSummary[] = [
  {
    id: "",
    name: "需求分析 Agent",
    description: "提取客户目标、范围和约束。",
    capabilities: ["analysis"],
  },
  {
    id: "",
    name: "风险识别 Agent",
    description: "识别合同、交付和合规风险。",
    capabilities: ["risk"],
  },
  {
    id: "",
    name: "数据分析 Agent",
    description: "整理指标证据和异常摘要。",
    capabilities: ["data"],
  },
]

const fallbackRevisionAgent: AgentSummary = {
  id: "",
  name: "返工修订 Agent",
  description: "根据人工意见修订方案。",
  capabilities: ["writing"],
}

function agentNode(index: number, agent: AgentSummary): WorkflowNode {
  return {
    id: `parallel_agent_${index + 1}`,
    type: "agent",
    title: agent.name || `并行 Agent ${index + 1}`,
    description: agent.description || "并行处理任务上下文。",
    agent_id: agent.id || null,
    config: {
      branch: `parallel_${index + 1}`,
      context_inputs: ["task.content", "context.summary"],
      context_outputs: ["subtask.output", "result_metadata"],
      capabilities: agent.capabilities || [],
    },
  }
}

export function buildWorkflowDefinition(parallelAgents: AgentSummary[], revisionAgent?: AgentSummary): WorkflowDefinition {
  const branchAgents = [...parallelAgents, ...fallbackParallelAgents].slice(0, 3)
  const reviseAgent = revisionAgent || fallbackRevisionAgent
  const parallelNodes = branchAgents.map((agent, index) => agentNode(index, agent))
  const nodes: WorkflowNode[] = [
    {
      id: "start",
      type: "start",
      title: "开始",
      description: "读取任务诉求并初始化 workflow 上下文。",
      config: {
        context_outputs: ["task.content", "request_metadata"],
      },
    },
    ...parallelNodes,
    {
      id: "review",
      type: "human",
      title: "人工确认",
      description: "汇总并行 Agent 结果，确认是否通过。",
      config: {
        context_inputs: parallelNodes.map((node) => node.id),
        required_metadata: ["decision"],
      },
    },
    {
      id: "judge",
      type: "condition",
      title: "条件判断",
      description: "根据人工确认结果路由到完成或返工。",
      config: {
        mode: "rule",
        source_node_id: "review",
        field: "decision",
        allowed_decisions: ["approved", "rejected", "need_more_info"],
        default_decision: "need_more_info",
      },
    },
    {
      id: "end",
      type: "end",
      title: "完成",
      description: "汇总上下文并生成最终输出。",
      config: {
        context_inputs: ["judge"],
      },
    },
    {
      id: "revise",
      type: "agent",
      title: reviseAgent.name || "返工修订 Agent",
      description: reviseAgent.description || "根据人工意见返工修订。",
      agent_id: reviseAgent.id || null,
      config: {
        branch: "revision",
        context_inputs: ["review.result_metadata", "context.summary"],
        context_outputs: ["subtask.output"],
        capabilities: reviseAgent.capabilities || [],
      },
    },
  ]

  const edges = [
    ...parallelNodes.map((node) => ({ from: "start", to: node.id, condition: {} })),
    ...parallelNodes.map((node) => ({ from: node.id, to: "review", condition: {} })),
    { from: "review", to: "judge", condition: {} },
    { from: "judge", to: "end", condition: { type: "decision", value: "approved" } },
    { from: "judge", to: "revise", condition: { type: "decision", value: "rejected" } },
    { from: "revise", to: "review", condition: {} },
  ]

  return { nodes, edges }
}
