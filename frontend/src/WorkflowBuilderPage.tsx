import {
  BarChart3,
  Bot,
  CheckCircle2,
  GitBranch,
  ListFilter,
  PanelRight,
  PanelRightClose,
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
import { useCallback, useMemo, useState } from "react"

import { Agent, WorkflowEdge, WorkflowNode, WorkflowTemplate, createWorkflow } from "./api/taskhub"
import { defaultWorkflowNodePositions, removeWorkflowNode } from "./workflowCanvas"
import { capabilityLabel } from "./workflowLabels"
import {
  WorkflowReactFlowEdge,
  WorkflowReactFlowNode,
  applyNodeConfig,
  reactFlowToWorkflow,
  workflowNodeDetailItems,
  workflowNodeInlineEditFields,
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

const nodeTypes = { workflowNode: WorkflowCanvasNode }

function emptyWorkflowDefinition(): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } {
  return {
    nodes: [
      {
        id: "start",
        type: "start",
        title: "开始",
        description: "读取任务诉求并初始化 workflow 上下文。",
        config: {
          context_inputs: ["task.content", "source_type", "request_metadata"],
          context_outputs: ["context.summary"],
        },
      },
      {
        id: "end",
        type: "end",
        title: "完成",
        description: "汇总上下文并生成最终输出。",
        config: {
          context_inputs: ["context.summary", "subtask.output"],
          context_outputs: ["final_output"],
        },
      },
    ],
    edges: [],
  }
}

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
  const [workflowName, setWorkflowName] = useState("")
  const [workflowDescription, setWorkflowDescription] = useState("")
  const [capabilityFilter, setCapabilityFilter] = useState("all")
  const initialDefinition = useMemo(() => emptyWorkflowDefinition(), [])
  const initialFlow = useMemo(() => workflowToReactFlow(initialDefinition, defaultWorkflowNodePositions), [initialDefinition])
  const [canvasNodes, setCanvasNodes] = useState<WorkflowNode[]>(initialDefinition.nodes)
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<WorkflowReactFlowNode>(initialFlow.nodes)
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<WorkflowReactFlowEdge>(initialFlow.edges)
  const [activeNodeId, setActiveNodeId] = useState("start")
  const [hoveredNodeId, setHoveredNodeId] = useState("")
  const [editingNodeId, setEditingNodeId] = useState("")
  const [nodeDrawerOpen, setNodeDrawerOpen] = useState(false)
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")

  const capabilityOptions = useMemo(() => {
    const capabilities = new Set<string>()
    availableAgents.forEach((agent) => (agent.capabilities || []).forEach((capability) => capabilities.add(capability)))
    return ["all", ...Array.from(capabilities).slice(0, 12)]
  }, [availableAgents])

  const definition = useMemo(() => reactFlowToWorkflow(canvasNodes, flowEdges), [canvasNodes, flowEdges])
  const activeNode = definition.nodes.find((node) => node.id === activeNodeId) || definition.nodes[0] || null
  const canDeleteActiveNode = Boolean(activeNode && activeNode.id !== "start" && activeNode.id !== "end")
  const filteredAgents = availableAgents.filter((agent) => agentMatchesCapability(agent, capabilityFilter))
  const handleNodeConfigChange = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    setCanvasNodes((current) => applyNodeConfig({ nodes: current, edges: [] }, nodeId, patch).nodes)
    setFlowNodes((current) =>
      current.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                ...flowDataPatch(patch),
              },
            }
          : node,
      ),
    )
    setActiveNodeId(nodeId)
  }, [setFlowNodes])
  const handleNodeEditStart = useCallback((nodeId: string) => {
    setActiveNodeId(nodeId)
    setHoveredNodeId("")
    setEditingNodeId(nodeId)
  }, [])
  const visibleFlowNodes = useMemo(
    () =>
      flowNodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          editing: node.id === editingNodeId,
          onConfigChange: handleNodeConfigChange,
          onEditStart: handleNodeEditStart,
          onEditEnd: () => setEditingNodeId(""),
        },
        className: node.id === hoveredNodeId || node.id === editingNodeId ? "workflow-flow-node-hovered" : undefined,
        zIndex: node.id === hoveredNodeId || node.id === editingNodeId ? 1000 : undefined,
      })),
    [editingNodeId, flowNodes, handleNodeConfigChange, handleNodeEditStart, hoveredNodeId],
  )

  function flowDataPatch(patch: Record<string, unknown>): Partial<WorkflowReactFlowNode["data"]> {
    return {
      ...(patch.execution_instruction !== undefined ? { instruction: String(patch.execution_instruction || "") } : {}),
      ...(patch.assignee !== undefined ? { assignee: String(patch.assignee || "") } : {}),
      ...(patch.handoff_instruction !== undefined ? { handoffInstruction: String(patch.handoff_instruction || "") } : {}),
      ...(patch.condition_description !== undefined ? { conditionDescription: String(patch.condition_description || "") } : {}),
      ...(patch.condition_content !== undefined ? { conditionContent: String(patch.condition_content || "") } : {}),
    }
  }

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
        assignee: "",
        handoff_instruction: "",
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
        condition_description: "",
        condition_content: "",
      },
    }
    appendCanvasNode(node)
    setToast("条件节点已加入画布")
  }

  function loadEmptyCanvas() {
    const nextDefinition = emptyWorkflowDefinition()
    const flow = workflowToReactFlow(nextDefinition, defaultWorkflowNodePositions)
    setWorkflowName("")
    setWorkflowDescription("")
    setCanvasNodes(nextDefinition.nodes)
    setFlowNodes(flow.nodes)
    setFlowEdges(flow.edges)
    setActiveNodeId("start")
    setMessage("已创建新画布")
  }

  function loadWorkflowTemplate(template: WorkflowTemplate) {
    const flow = workflowToReactFlow(template.definition, defaultWorkflowNodePositions)
    setWorkflowName(template.name || "")
    setWorkflowDescription(template.description || "")
    setCanvasNodes(template.definition.nodes || [])
    setFlowNodes(flow.nodes)
    setFlowEdges(flow.edges)
    setActiveNodeId(template.definition.nodes?.[0]?.id || "start")
    setMessage(`已加载模板：${template.name}`)
    setToast(`${template.name} 已渲染到画布`)
  }

  function deleteActiveNode() {
    if (!activeNode || !canDeleteActiveNode) return
    const result = removeWorkflowNode(canvasNodes, definition.edges, activeNode.id)
    setCanvasNodes(result.nodes)
    setFlowNodes((current) => current.filter((node) => node.id !== activeNode.id))
    setFlowEdges((current) => current.filter((edge) => edge.source !== activeNode.id && edge.target !== activeNode.id))
    setActiveNodeId("start")
    setEditingNodeId("")
    setMessage(`${activeNode.title || activeNode.id} 已删除`)
    setToast(`${activeNode.title || activeNode.id} 已从画布删除`)
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
  const layoutClassName = [
    "workflow-layout",
    nodeDrawerOpen ? "" : "workflow-layout-node-collapsed",
    templateDrawerOpen ? "" : "workflow-layout-template-collapsed",
  ].filter(Boolean).join(" ")

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

      <div className={layoutClassName}>
        <section className="panel workflow-canvas-panel">
          <div className="workflow-canvas-header">
            <div>
              <label className="field compact-field">
                <span>Workflow 名称</span>
                <input className="input" value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="请输入 Workflow 名称" />
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
            <input className="input" value={workflowDescription} onChange={(event) => setWorkflowDescription(event.target.value)} placeholder="请输入 Workflow 描述" />
          </label>
          <div className="workflow-canvas" aria-label="Workflow 自由画布">
            <aside className="workflow-left-drawers">
              <section className="panel workflow-agent-panel workflow-drawer-panel">
                <div className="panel-title">
                  <span className="nav-text">
                    <Bot size={16} />
                    可编排节点
                  </span>
                  <button className="btn btn-small btn-icon" type="button" onClick={() => setNodeDrawerOpen((value) => !value)} title={nodeDrawerOpen ? "收起可编排节点" : "展开可编排节点"}>
                    {nodeDrawerOpen ? <PanelRightClose size={14} /> : <PanelRight size={14} />}
                  </button>
                </div>
                {nodeDrawerOpen && (
                  <>
                    <p className="muted drawer-summary">{availableAgents.length} 个可选节点</p>
                    <label className="field">
                      <span>筛选能力</span>
                      <select className="input" value={capabilityFilter} onChange={(event) => setCapabilityFilter(event.target.value)}>
                        {capabilityOptions.map((capability) => (
                          <option key={capability} value={capability}>
                            {capabilityLabel(capability)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="workflow-agent-list">
                      {!filteredAgents.length && <EmptyPanel text="暂无可选节点，可先到流程节点管理创建" />}
                      {filteredAgents.map((agent) => {
                        const isOnCanvas = canvasNodes.some((node) => node.agent_id === agent.id)
                        return (
                          <div className={isOnCanvas ? "workflow-agent-card active" : "workflow-agent-card"} key={agent.id}>
                            <div className="workflow-agent-head">
                              <span className="workflow-agent-name">
                                <span className="workflow-icon success"><Bot size={16} /></span>
                                <span>{agent.name}</span>
                              </span>
                              <span className={isOnCanvas ? "tag workflow-agent-status-tag active" : "tag workflow-agent-status-tag"}>
                                {isOnCanvas ? "已在画布" : agent.agent_type || "processing"}
                              </span>
                            </div>
                            <p>{agent.description || "暂无描述"}</p>
                            <div className="tag-row">
                              {(agent.capabilities || []).slice(0, 4).map((capability) => <span className="tag" key={capability}>{capabilityLabel(capability)}</span>)}
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
                  </>
                )}
              </section>

              <section className="panel workflow-template-panel workflow-drawer-panel">
                <div className="panel-title">
                  <span className="nav-text">
                    <ListFilter size={16} />
                    流程模板
                  </span>
                  <button className="btn btn-small btn-icon" type="button" onClick={() => setTemplateDrawerOpen((value) => !value)} title={templateDrawerOpen ? "收起流程模板" : "展开流程模板"}>
                    {templateDrawerOpen ? <PanelRightClose size={14} /> : <PanelRight size={14} />}
                  </button>
                </div>
                {templateDrawerOpen && (
                  <div className="workflow-agent-list">
                    <button className="btn btn-small workflow-new-canvas-button" type="button" onClick={loadEmptyCanvas}>
                      <Sparkles size={14} />
                      新建空白画布
                    </button>
                    {!workflows.length && <EmptyPanel text="暂无已保存流程模板" />}
                    {workflows.map((workflow) => (
                      <div className="workflow-agent-card workflow-template-card" key={workflow.id}>
                        <div className="workflow-agent-head">
                          <span className="workflow-agent-name">
                            <span className="workflow-icon violet"><GitBranch size={16} /></span>
                            <span>{workflow.name}</span>
                          </span>
                          <span className="tag">{workflow.status || "active"}</span>
                        </div>
                        <p>{workflow.description || "暂无描述"}</p>
                        <div className="tag-row">
                          <span className="tag">nodes={workflow.definition.nodes.length}</span>
                          <span className="tag">edges={workflow.definition.edges.length}</span>
                        </div>
                        <div className="workflow-card-actions">
                          <button className="btn btn-small" type="button" onClick={() => loadWorkflowTemplate(workflow)}>
                            <Plus size={14} />
                            渲染到画布
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </aside>
            <ReactFlow
              nodes={visibleFlowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_event, node) => setActiveNodeId(node.id)}
              onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
              onNodeMouseLeave={() => setHoveredNodeId("")}
              fitView
              minZoom={0.2}
              maxZoom={1.8}
            >
              <Background gap={18} />
              <MiniMap pannable zoomable />
              <Controls />
            </ReactFlow>
          </div>
          <div className="workflow-save-summary">
            <span>nodes={definition.nodes.length}，edges={definition.edges.length}，templates={workflows.length}</span>
            {message && <span className={message.includes("失败") || message.includes("Workflow not found") ? "danger-text" : "muted"}>{message}</span>}
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
  const editableFields = workflowNodeInlineEditFields(data)
  const editing = Boolean(data.editing)
  const className = [
    "workflow-node",
    String(data.kind),
    data.kind === "condition" ? "condition" : "",
    editableFields.length > 0 ? "editable" : "",
    editing ? "editing" : "",
  ].filter(Boolean).join(" ")
  const showTargetHandle = data.kind !== "start"
  const showSourceHandle = data.kind !== "end"
  const detailItems = workflowNodeDetailItems(data)

  function beginInlineEdit(event: React.MouseEvent<HTMLDivElement>) {
    if (!editableFields.length) return
    event.stopPropagation()
    data.onEditStart?.(data.id)
  }

  return (
    <div className={className} aria-label={`${data.title || data.id} ${nodeKindText(String(data.kind))}`} onDoubleClick={beginInlineEdit}>
      {showTargetHandle && <Handle type="target" position={Position.Left} />}
      {data.kind === "condition" ? (
        <span className="workflow-condition-inner">
          <span className="workflow-icon violet">{nodeIcon(String(data.kind), data.id)}</span>
          <strong>{data.title || data.id}</strong>
          <small>{data.conditionDescription || "decision"}</small>
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
          {data.kind === "human" && data.assignee && <small className="workflow-node-instruction">人员：{data.assignee}</small>}
          {data.kind === "human" && data.handoffInstruction && <small className="workflow-node-instruction">{data.handoffInstruction}</small>}
          {data.kind === "agent" && data.instruction && <small className="workflow-node-instruction">{data.instruction}</small>}
        </>
      )}
      {editableFields.length > 0 && !editing && <small className="workflow-node-edit-hint">双击编辑</small>}
      {editing && (
        <div
          className="workflow-node-inline-editor nodrag nowheel"
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <strong>节点信息</strong>
          {editableFields.map((field) => (
            <label className="workflow-inline-field" key={field.key}>
              <span>{field.label}</span>
              {field.inputType === "textarea" ? (
                <textarea
                  value={field.value}
                  onChange={(event) => data.onConfigChange?.(data.id, { [field.key]: event.target.value })}
                  onKeyDown={(event) => event.stopPropagation()}
                  placeholder={field.placeholder}
                />
              ) : (
                <input
                  value={field.value}
                  onChange={(event) => data.onConfigChange?.(data.id, { [field.key]: event.target.value })}
                  onKeyDown={(event) => event.stopPropagation()}
                  placeholder={field.placeholder}
                />
              )}
            </label>
          ))}
          <div className="workflow-inline-actions">
            <button
              className="btn btn-small"
              type="button"
              onClickCapture={(event) => {
                event.stopPropagation()
                data.onEditEnd?.()
              }}
              onClick={(event) => event.stopPropagation()}
              onMouseDownCapture={(event) => {
                event.stopPropagation()
                data.onEditEnd?.()
              }}
              onPointerDown={(event) => {
                event.stopPropagation()
                data.onEditEnd?.()
              }}
            >
              完成
            </button>
          </div>
        </div>
      )}
      {detailItems.length > 0 && (
        <div className="workflow-node-popover" role="tooltip">
          <strong>{data.title || data.id}</strong>
          <dl>
            {detailItems.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
        </div>
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
