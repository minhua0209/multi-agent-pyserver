import {
  BarChart3,
  Bot,
  CheckCircle2,
  GitBranch,
  ListFilter,
  PanelRight,
  Pencil,
  Plus,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
} from "lucide-react"
import {
  Background,
  Connection,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  NodeProps,
  Position,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { useCallback, useEffect, useMemo, useState } from "react"

import { Agent, WorkflowEdge, WorkflowNode, WorkflowTemplate, createWorkflow } from "./api/taskhub"
import { defaultWorkflowNodePositions, removeWorkflowNode } from "./workflowCanvas"
import { AgentSummary, buildWorkflowDefinition } from "./workflowBuilder"
import {
  WorkflowReactFlowEdge,
  WorkflowReactFlowNode,
  applyNodeInstruction,
  reactFlowToWorkflow,
  workflowToReactFlow,
} from "./workflowReactFlow"

interface WorkflowBuilderPageProps {
  agents: Agent[]
  workflows: WorkflowTemplate[]
  onWorkflowSaved: (workflow: WorkflowTemplate) => void
  setToast: (value: string) => void
  modal?: boolean
  submittingTask?: boolean
  onSubmitTask?: (definition: { nodes: WorkflowNode[]; edges: WorkflowEdge[] }) => Promise<void>
}

function processingAgents(agents: Agent[]) {
  return agents.filter((agent) => agent.agent_type !== "condition")
}

function agentMatchesCapability(agent: Agent, capability: string) {
  if (capability === "all") return true
  const search = capability.toLowerCase()
  return [agent.name, agent.description, ...(agent.capabilities || [])]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(search))
}

function toAgentSummary(agent: Agent): AgentSummary {
  return {
    id: agent.id,
    name: agent.name,
    description: agent.description,
    capabilities: agent.capabilities || [],
  }
}

function nodeIcon(type: string, id: string) {
  if (type === "start") return <Sparkles size={16} />
  if (type === "human") return <UserCheck size={16} />
  if (type === "condition") return <GitBranch size={16} />
  if (type === "end") return <CheckCircle2 size={16} />
  if (id === "parallel_agent_2") return <ShieldCheck size={16} />
  if (id === "parallel_agent_3") return <BarChart3 size={16} />
  if (id === "revise") return <Pencil size={16} />
  return <Bot size={16} />
}

function nodeKindText(type: string) {
  return { start: "开始", agent: "Agent", human: "人工", condition: "条件", end: "结束" }[type] || type
}

function contextList(value: unknown, fallback: string) {
  if (Array.isArray(value)) {
    const text = value.map(String).filter(Boolean).join("、")
    return text || fallback
  }
  if (typeof value === "string" && value.trim()) return value
  return fallback
}

function nodeContext(node?: WorkflowNode) {
  if (!node) {
    return {
      input: "-",
      output: "-",
      condition: "请选择画布节点",
    }
  }
  const config = node.config || {}
  const input = contextList(config.context_inputs, "继承上游节点输出和任务上下文")
  const output = contextList(config.context_outputs, "写入 context.summary 或节点执行结果")
  if (node.id === "start") {
    return {
      input: "task.content、source_type、request_metadata",
      output: "初始化 context.summary，写入任务目标与执行约束",
      condition: "无条件，作为画布起点",
    }
  }
  if (node.type === "agent") {
    return {
      input,
      output,
      condition: "从画布连线读取上游上下文，执行完成后输出给下游节点",
    }
  }
  if (node.type === "human") {
    return {
      input,
      output: "result_metadata.decision、人工备注、修订要求",
      condition: "人工确认后继续流转，可作为条件节点的判断来源",
    }
  }
  if (node.type === "condition") {
    const field = typeof config.field === "string" ? config.field : "decision"
    const decisions = contextList(config.allowed_decisions, "approved、rejected")
    return {
      input,
      output: `${field} 命中后进入对应下游节点`,
      condition: `field=${field}，可选值：${decisions}`,
    }
  }
  return {
    input,
    output: "final_output、context.summary、产物引用",
    condition: "作为画布终点汇总结果",
  }
}

const nodeTypes = { workflowNode: WorkflowCanvasNode }

export function WorkflowBuilderPage({
  agents,
  workflows,
  onWorkflowSaved,
  setToast,
  modal = false,
  submittingTask = false,
  onSubmitTask,
}: WorkflowBuilderPageProps) {
  const availableAgents = useMemo(() => processingAgents(agents), [agents])
  const [workflowName, setWorkflowName] = useState("客户交付 Workflow")
  const [workflowDescription, setWorkflowDescription] = useState("并行分析客户需求、风险和数据证据，人工确认后按条件完成或返工。")
  const [capabilityFilter, setCapabilityFilter] = useState("all")
  const [canvasNodes, setCanvasNodes] = useState<WorkflowNode[]>([])
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<WorkflowReactFlowNode>([])
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<WorkflowReactFlowEdge>([])
  const [activeNodeId, setActiveNodeId] = useState("start")
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")

  useEffect(() => {
    if (canvasNodes.length > 0) return
    const initialDefinition = buildWorkflowDefinition(
      availableAgents.slice(0, 3).map(toAgentSummary),
      availableAgents[3] ? toAgentSummary(availableAgents[3]) : undefined,
    )
    setCanvasNodes(initialDefinition.nodes)
    const flow = workflowToReactFlow(initialDefinition, defaultWorkflowNodePositions)
    setFlowNodes(flow.nodes)
    setFlowEdges(flow.edges)
  }, [availableAgents, canvasNodes.length])

  const capabilityOptions = useMemo(() => {
    const capabilities = new Set<string>()
    availableAgents.forEach((agent) => (agent.capabilities || []).forEach((capability) => capabilities.add(capability)))
    return ["all", ...Array.from(capabilities).slice(0, 12)]
  }, [availableAgents])

  const definition = useMemo(() => reactFlowToWorkflow(canvasNodes, flowEdges), [canvasNodes, flowEdges])
  const activeNode = definition.nodes.find((node) => node.id === activeNodeId) || definition.nodes[0] || null
  const selectedNode: WorkflowNode = activeNode || {
    id: "empty",
    type: "start",
    title: "未选择节点",
    description: "从画布选择节点，或从左侧 Agent 列表加入新节点。",
  }
  const context = nodeContext(activeNode)
  const canDeleteActiveNode = Boolean(activeNode && activeNode.id !== "start" && activeNode.id !== "end")
  const filteredAgents = availableAgents.filter((agent) => agentMatchesCapability(agent, capabilityFilter))

  function createNodeId(prefix: string) {
    const normalized = prefix.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 28) || "node"
    const existingIds = new Set(canvasNodes.map((node) => node.id))
    let index = 1
    let nextId = `${normalized}_${index}`
    while (existingIds.has(nextId)) {
      index += 1
      nextId = `${normalized}_${index}`
    }
    return nextId
  }

  function nextNodePosition() {
    const sourcePosition = flowNodes.find((node) => node.id === activeNodeId)?.position || { x: 46, y: 268 }
    const offset = ((canvasNodes.length % 3) - 1) * 118
    return {
      x: Math.max(0, sourcePosition.x + 230),
      y: Math.max(0, sourcePosition.y + offset),
    }
  }

  function appendCanvasNode(node: WorkflowNode) {
    const sourceId = activeNode && activeNode.id !== "end" ? activeNode.id : "start"
    const position = nextNodePosition()
    const flow = workflowToReactFlow({ nodes: [node], edges: [] }, { [node.id]: { left: position.x, top: position.y } })
    setCanvasNodes((current) => [...current, node])
    setFlowNodes((current) => [...current, flow.nodes[0]])
    setFlowEdges((current) => {
      if (!sourceId || sourceId === node.id) return current
      if (!canvasNodes.some((item) => item.id === sourceId)) return current
      if (current.some((edge) => edge.source === sourceId && edge.target === node.id)) return current
      return [...current, makeFlowEdge(sourceId, node.id)]
    })
    setActiveNodeId(node.id)
  }

  function addAgentNode(agent: Agent) {
    setMessage("")
    const node: WorkflowNode = {
      id: createNodeId(`agent_${agent.id || agent.name}`),
      type: "agent",
      title: agent.name || "Agent 节点",
      description: agent.description || "处理任务上下文并输出执行结果。",
      agent_id: agent.id,
      config: {
        context_inputs: ["task.content", "context.summary"],
        context_outputs: ["subtask.output", "result_metadata"],
        capabilities: agent.capabilities || [],
      },
    }
    appendCanvasNode(node)
    setToast(`${agent.name} 已加入画布`)
  }

  function addHumanNode() {
    setMessage("")
    const node: WorkflowNode = {
      id: createNodeId("human_review"),
      type: "human",
      title: "人工确认",
      description: "人工查看上游结果并补充通过、驳回或备注信息。",
      config: {
        context_inputs: ["context.summary", "subtask.output"],
        context_outputs: ["result_metadata.decision", "human_comment"],
        required_metadata: ["decision"],
      },
    }
    appendCanvasNode(node)
    setToast("人工节点已加入画布")
  }

  function addConditionNode() {
    setMessage("")
    const node: WorkflowNode = {
      id: createNodeId("condition"),
      type: "condition",
      title: "条件判断",
      description: "根据上游 decision 或上下文字段决定下一步流转。",
      config: {
        mode: "rule",
        field: "decision",
        allowed_decisions: ["approved", "rejected", "need_more_info"],
        default_decision: "need_more_info",
      },
    }
    appendCanvasNode(node)
    setToast("条件节点已加入画布")
  }

  function deleteActiveNode() {
    if (!activeNode || !canDeleteActiveNode) return
    const result = removeWorkflowNode(canvasNodes, definition.edges, activeNode.id)
    setCanvasNodes(result.nodes)
    setFlowNodes((current) => current.filter((node) => node.id !== activeNode.id))
    setFlowEdges((current) => current.filter((edge) => edge.source !== activeNode.id && edge.target !== activeNode.id))
    setActiveNodeId("start")
    setMessage(`${activeNode.title || activeNode.id} 已删除`)
    setToast(`${activeNode.title || activeNode.id} 已从画布删除`)
  }

  function updateActiveInstruction(value: string) {
    if (!activeNode || activeNode.type !== "agent") return
    const updated = applyNodeInstruction(definition, activeNode.id, value)
    setCanvasNodes(updated.nodes)
    setFlowNodes((current) =>
      current.map((node) =>
        node.id === activeNode.id
          ? {
              ...node,
              data: {
                ...node.data,
                instruction: value,
              },
            }
          : node,
      ),
    )
  }

  const onConnect = useCallback(
    (connection: Connection) => {
      setFlowEdges((current) =>
        addEdge(
          {
            ...connection,
            type: "smoothstep",
            markerEnd: { type: MarkerType.ArrowClosed },
            data: { condition: {} },
          },
          current,
        ),
      )
    },
    [setFlowEdges],
  )

  async function submitWorkflowTask() {
    if (!onSubmitTask) return
    setSaving(true)
    setMessage("")
    try {
      await onSubmitTask(definition)
      setMessage("已提交任务")
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "提交失败")
    } finally {
      setSaving(false)
    }
  }

  async function saveWorkflow() {
    setSaving(true)
    setMessage("")
    try {
      const saved = await createWorkflow({
        name: workflowName,
        description: workflowDescription,
        definition,
      })
      onWorkflowSaved(saved)
      setToast("Workflow 已保存，可在任务发布时选择")
      setMessage(`已保存：${saved.id}`)
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }

  const summaryText = `画布节点 ${definition.nodes.length} 个，连线 ${definition.edges.length} 条；选中节点后新增会自动从当前节点连接。`

  return (
    <div className={modal ? "workflow-page workflow-page-modal" : "page active workflow-page"}>
      <PageTitle
        title="Agent 节点编排"
        description="选择 Agent 节点，在自由画布中配置执行节点、人工确认、条件判断和上下文流转。"
      >
        <button className="btn" type="button" onClick={() => setMessage(`保存参数：nodes=${definition.nodes.length}，edges=${definition.edges.length}`)}>
          <PanelRight size={16} />
          查看保存参数
        </button>
        <button className="btn btn-primary" type="button" onClick={saveWorkflow} disabled={saving || !workflowName.trim()}>
          <Save size={16} />
          {saving ? "保存中" : "保存 Workflow"}
        </button>
      </PageTitle>

      <div className="workflow-layout">
        <section className="panel workflow-agent-panel">
          <div className="panel-title">
            <span className="nav-text">
              <Bot size={16} />
              Agent 节点列表
            </span>
            <span className="muted">{availableAgents.length} 个可选</span>
          </div>
          <label className="field">
            <span>筛选能力</span>
            <select className="input" value={capabilityFilter} onChange={(event) => setCapabilityFilter(event.target.value)}>
              {capabilityOptions.map((capability) => (
                <option key={capability} value={capability}>
                  {capability === "all" ? "全部能力" : capability}
                </option>
              ))}
            </select>
          </label>
          <div className="workflow-agent-list">
            {!filteredAgents.length && <EmptyPanel text="暂无可选 Agent，可先到 Agent 管理创建" />}
            {filteredAgents.map((agent) => {
              const isOnCanvas = canvasNodes.some((node) => node.agent_id === agent.id)
              return (
                <div className={isOnCanvas ? "workflow-agent-card active" : "workflow-agent-card"} key={agent.id}>
                  <div className="workflow-agent-head">
                    <span className="workflow-agent-name">
                      <span className="workflow-icon success"><Bot size={16} /></span>
                      <span>{agent.name}</span>
                    </span>
                    <span className="tag">{isOnCanvas ? "已在画布" : agent.agent_type || "processing"}</span>
                  </div>
                  <p>{agent.description || "暂无描述"}</p>
                  <div className="tag-row">
                    {(agent.capabilities || []).slice(0, 4).map((capability) => <span className="tag" key={capability}>{capability}</span>)}
                  </div>
                  <div className="workflow-card-actions">
                    <button className="btn btn-small" type="button" onClick={() => addAgentNode(agent)}>
                      <Plus size={14} />
                      加入画布
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        <section className="panel workflow-canvas-panel">
          <div className="workflow-canvas-header">
            <div>
              <label className="field compact-field">
                <span>Workflow 名称</span>
                <input className="input" value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} />
              </label>
              <p className="muted">{summaryText}</p>
            </div>
            <div className="workflow-canvas-tools">
              <button className="btn btn-small" type="button" onClick={addHumanNode}>
                <UserCheck size={14} />
                人工节点
              </button>
              <button className="btn btn-small" type="button" onClick={addConditionNode}>
                <GitBranch size={14} />
                条件节点
              </button>
              <button className="btn btn-small btn-danger" type="button" onClick={deleteActiveNode} disabled={!canDeleteActiveNode}>
                <Trash2 size={14} />
                删除选中
              </button>
              {onSubmitTask && (
                <button className="btn btn-small btn-primary" type="button" onClick={() => void submitWorkflowTask()} disabled={saving || submittingTask || !workflowName.trim()}>
                  <SendIcon />
                  {submittingTask || saving ? "提交中" : "提交任务"}
                </button>
              )}
            </div>
          </div>
          <label className="field compact-field">
            <span>描述</span>
            <input className="input" value={workflowDescription} onChange={(event) => setWorkflowDescription(event.target.value)} />
          </label>
          <div className="workflow-canvas" aria-label="Workflow 自由画布">
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_event, node) => setActiveNodeId(node.id)}
              fitView
              minZoom={0.2}
              maxZoom={1.8}
            >
              <Background gap={18} />
              <MiniMap pannable zoomable />
              <Controls />
            </ReactFlow>
          </div>
        </section>

        <section className="panel workflow-context-panel">
          <div className="panel-title">
            <span className="nav-text">
              <ListFilter size={16} />
              上下文与条件
            </span>
            <span className="tag">{nodeKindText(selectedNode.type)}</span>
          </div>
          <div className="workflow-info-box">
            <strong>{selectedNode.title || selectedNode.id}</strong>
            <p>{selectedNode.description || "-"}</p>
            <div className="tag-row">
              <span className="tag">{selectedNode.id}</span>
              {selectedNode.agent_id && <span className="tag">{selectedNode.agent_id}</span>}
            </div>
          </div>
          <div className="workflow-info-box">
            <strong>输入上下文</strong>
            <p>{context.input}</p>
          </div>
          <div className="workflow-info-box">
            <strong>输出上下文</strong>
            <p>{context.output}</p>
          </div>
          <div className="workflow-info-box">
            <strong>条件配置</strong>
            <p>{context.condition}</p>
          </div>
          {selectedNode.type === "agent" && (
            <label className="field workflow-info-box">
              <strong>执行交代</strong>
              <textarea
                className="textarea"
                value={String(selectedNode.config?.execution_instruction || "")}
                onChange={(event) => updateActiveInstruction(event.target.value)}
                placeholder="给该 Agent 的执行要求，例如重点检查字段、输出格式、注意事项"
              />
            </label>
          )}
          <div className="workflow-info-box">
            <strong>保存参数预览</strong>
            <p>nodes={definition.nodes.length}，edges={definition.edges.length}，templates={workflows.length}</p>
            {message && <p className={message.includes("失败") || message.includes("Workflow not found") ? "danger-text" : "muted"}>{message}</p>}
          </div>
        </section>
      </div>
    </div>
  )
}

function PageTitle({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h2 className="page-title">{title}</h2>
        <p className="page-description">{description}</p>
      </div>
      <div className="page-header-actions">{children}</div>
    </div>
  )
}

function EmptyPanel({ text }: { text: string }) {
  return <div className="workflow-empty">{text}</div>
}

function WorkflowCanvasNode({ data }: NodeProps<WorkflowReactFlowNode>) {
  const className = [
    "workflow-node",
    String(data.kind),
    data.kind === "condition" ? "condition" : "",
  ].filter(Boolean).join(" ")
  const showTargetHandle = data.kind !== "start"
  const showSourceHandle = data.kind !== "end"

  return (
    <div className={className} aria-label={`${data.title || data.id} ${nodeKindText(String(data.kind))}`}>
      {showTargetHandle && <Handle type="target" position={Position.Left} />}
      {data.kind === "condition" ? (
        <span className="workflow-condition-inner">
          <span className="workflow-icon violet">{nodeIcon(String(data.kind), data.id)}</span>
          <strong>{data.title || data.id}</strong>
          <small>decision</small>
        </span>
      ) : (
        <>
          <span className="workflow-node-head">
            <span className={data.kind === "human" ? "workflow-icon warning" : data.kind === "end" ? "workflow-icon success" : "workflow-icon"}>
              {nodeIcon(String(data.kind), data.id)}
            </span>
            <strong>{data.title || data.id}</strong>
          </span>
          <small>{data.description || "-"}</small>
          {data.kind === "agent" && data.instruction && <small className="workflow-node-instruction">{data.instruction}</small>}
        </>
      )}
      {showSourceHandle && <Handle type="source" position={Position.Right} />}
    </div>
  )
}

function makeFlowEdge(source: string, target: string): WorkflowReactFlowEdge {
  return {
    id: `${source}-${target}`,
    source,
    target,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { condition: {} },
  }
}

function SendIcon() {
  return <Send size={16} />
}
