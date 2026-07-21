import {
  BarChart3,
  Bot,
  CheckCircle2,
  GitBranch,
  ListFilter,
  Pencil,
  Plus,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
  PanelLeftClose,
  PanelLeftOpen,
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
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react"

import { Agent, UserOption, WorkflowEdge, WorkflowNode, WorkflowTemplate, createWorkflow, updateWorkflow } from "./api/taskhub"
import { removeWorkflowNode } from "./workflowCanvas"
import { capabilityLabel } from "./workflowLabels"
import {
  WorkflowReactFlowEdge,
  WorkflowReactFlowNode,
  applyNodeConfig,
  autoLayoutWorkflowNodePositions,
  canEditDecisionEdge,
  reduceWorkflowInlineTextDraft,
  reactFlowToWorkflow,
  setDecisionEdgeCondition,
  workflowConditionDecisionValues,
  normalizeWorkflowConditionOptions,
  workflowNodeDetailItems,
  workflowNodeInlineEditFields,
  workflowToReactFlow,
} from "./workflowReactFlow"
import { workflowResourcePanelClass, workflowResourceToggleLabel } from "./workflowResourcePanel"
import { workflowBuilderCopy, workflowTemplateSaveAction } from "./workflowSave"
import { workflowTemplateCardView } from "./workflowTemplateCard"

interface WorkflowBuilderPageProps {
  agents: Agent[]
  users?: UserOption[]
  workflows: WorkflowTemplate[]
  onWorkflowSaved: (workflow: WorkflowTemplate) => void
  setToast: (value: string) => void
  modal?: boolean
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

function withAgentNames(nodes: WorkflowReactFlowNode[], agentNameById: Map<string, string>) {
  return nodes.map((node) => {
    const agentId = node.data.agentId ? String(node.data.agentId) : ""
    const agentName = agentId ? agentNameById.get(agentId) : ""
    if (!agentName) return node
    return {
      ...node,
      data: {
        ...node.data,
        agentName,
      },
    }
  })
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

const compactStarterNodePositions = {
  start: { left: 80, top: 180 },
  end: { left: 420, top: 180 },
}

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
  users = [],
  workflows,
  onWorkflowSaved,
  setToast,
  modal = false,
}: WorkflowBuilderPageProps) {
  const availableAgents = useMemo(() => processingAgents(agents), [agents])
  const agentNameById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent.name])), [agents])
  const [workflowName, setWorkflowName] = useState("")
  const [workflowDescription, setWorkflowDescription] = useState("")
  const [capabilityFilter, setCapabilityFilter] = useState("all")
  const initialDefinition = useMemo(() => emptyWorkflowDefinition(), [])
  const initialFlow = useMemo(() => workflowToReactFlow(initialDefinition, compactStarterNodePositions), [initialDefinition])
  const [canvasNodes, setCanvasNodes] = useState<WorkflowNode[]>(initialDefinition.nodes)
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<WorkflowReactFlowNode>(initialFlow.nodes)
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState<WorkflowReactFlowEdge>(initialFlow.edges)
  const [activeNodeId, setActiveNodeId] = useState("start")
  const [hoveredNodeId, setHoveredNodeId] = useState("")
  const [editingNodeId, setEditingNodeId] = useState("")
  const [activeResourcePanel, setActiveResourcePanel] = useState<"agents" | "templates">("agents")
  const [resourcePanelCollapsed, setResourcePanelCollapsed] = useState(false)
  const [activeTemplateId, setActiveTemplateId] = useState("")
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [editingEdgeId, setEditingEdgeId] = useState("")
  const [edgeDecisionDraft, setEdgeDecisionDraft] = useState("")

  const capabilityOptions = useMemo(() => {
    const capabilities = new Set<string>()
    availableAgents.forEach((agent) => (agent.capabilities || []).forEach((capability) => capabilities.add(capability)))
    return ["all", ...Array.from(capabilities).slice(0, 12)]
  }, [availableAgents])

  const definition = useMemo(() => reactFlowToWorkflow(canvasNodes, flowEdges), [canvasNodes, flowEdges])
  const activeNode = definition.nodes.find((node) => node.id === activeNodeId) || definition.nodes[0] || null
  const editingEdge = flowEdges.find((edge) => edge.id === editingEdgeId) || null
  const canDeleteActiveNode = Boolean(activeNode && activeNode.id !== "start" && activeNode.id !== "end")
  const filteredAgents = availableAgents.filter((agent) => agentMatchesCapability(agent, capabilityFilter))
  const edgeDecisionOptions = decisionOptionsForEdge(canvasNodes, editingEdge)
  const handleNodeConfigChange = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    const normalizedPatch = normalizeNodePatch(patch, users)
    setCanvasNodes((current) => applyNodeConfig({ nodes: current, edges: [] }, nodeId, normalizedPatch).nodes)
    setFlowNodes((current) =>
      current.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                ...flowDataPatch(normalizedPatch),
              },
            }
          : node,
      ),
    )
    setActiveNodeId(nodeId)
  }, [setFlowNodes, users])
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
          userOptions: users,
        },
        className: node.id === hoveredNodeId || node.id === editingNodeId ? "workflow-flow-node-hovered" : undefined,
        zIndex: node.id === hoveredNodeId || node.id === editingNodeId ? 1000 : undefined,
      })),
    [editingNodeId, flowNodes, handleNodeConfigChange, handleNodeEditStart, hoveredNodeId],
  )

  function flowDataPatch(patch: Record<string, unknown>): Partial<WorkflowReactFlowNode["data"]> {
    return {
      ...(patch.execution_instruction !== undefined ? { instruction: String(patch.execution_instruction || "") } : {}),
      ...(patch.assignee_user_id !== undefined ? { assigneeUserId: String(patch.assignee_user_id || "") } : {}),
      ...(patch.assignee_user_name !== undefined
        ? {
            assigneeUserName: String(patch.assignee_user_name || ""),
            assignee: String(patch.assignee_user_name || ""),
          }
        : {}),
      ...(patch.assignee_role !== undefined ? { assigneeRole: String(patch.assignee_role || "") } : {}),
      ...(patch.handoff_instruction !== undefined ? { handoffInstruction: String(patch.handoff_instruction || "") } : {}),
      ...(patch.condition_description !== undefined ? { conditionDescription: String(patch.condition_description || "") } : {}),
      ...(patch.condition_content !== undefined ? { conditionContent: String(patch.condition_content || "") } : {}),
      ...(patch.condition_options !== undefined ? { conditionOptions: normalizeWorkflowConditionOptions(patch.condition_options) } : {}),
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
    setFlowNodes((current) => [...current, withAgentNames(flow.nodes, agentNameById)[0]])
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
    setActiveTemplateId("")
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
        agent_name: agent.name,
      },
    }
    appendCanvasNode(node)
    setToast(`${agent.name} 已加入画布`)
  }

  function addHumanNode() {
    setMessage("")
    setActiveTemplateId("")
    const node: WorkflowNode = {
      id: createNodeId("human_review"),
      type: "human",
      title: "人工确认",
      description: "人工查看上游结果并补充通过、驳回或备注信息。",
      config: {
        context_inputs: ["context.summary", "subtask.output"],
        context_outputs: ["result_metadata.decision", "human_comment"],
        required_metadata: ["decision"],
        assignee_user_id: "",
        assignee_user_name: "",
        assignee_role: "",
        handoff_instruction: "",
      },
    }
    appendCanvasNode(node)
    setToast("人工节点已加入画布")
  }

  function addConditionNode() {
    setMessage("")
    setActiveTemplateId("")
    const node: WorkflowNode = {
      id: createNodeId("condition"),
      type: "condition",
      title: "条件判断",
      description: "根据条件内容、任务摘要和最近一轮输出决定下一步流转。",
      config: {
        condition_description: "",
        condition_options: [],
      },
    }
    appendCanvasNode(node)
    setToast("条件节点已加入画布")
  }

  function loadEmptyCanvas() {
    const nextDefinition = emptyWorkflowDefinition()
    const flow = workflowToReactFlow(nextDefinition, compactStarterNodePositions)
    setWorkflowName("")
    setWorkflowDescription("")
    setCanvasNodes(nextDefinition.nodes)
    setFlowNodes(withAgentNames(flow.nodes, agentNameById))
    setFlowEdges(flow.edges)
    setActiveNodeId("start")
    setActiveTemplateId("")
    setMessage("已创建新画布")
  }

  function loadWorkflowTemplate(template: WorkflowTemplate) {
    const flow = workflowToReactFlow(template.definition, autoLayoutWorkflowNodePositions(template.definition))
    setWorkflowName(template.name || "")
    setWorkflowDescription(template.description || "")
    setCanvasNodes(template.definition.nodes || [])
    setFlowNodes(withAgentNames(flow.nodes, agentNameById))
    setFlowEdges(flow.edges)
    setActiveNodeId(template.definition.nodes?.[0]?.id || "start")
    setActiveTemplateId(template.id)
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

  const handleEdgeClick = useCallback(
    (event: React.MouseEvent, edge: WorkflowReactFlowEdge) => {
      event.stopPropagation()
      if (!canEditDecisionEdge(canvasNodes, edge)) {
        setMessage("只有判断节点后的连线支持条件配置")
        return
      }
      setEditingEdgeId(edge.id)
      setEdgeDecisionDraft(edgeDecisionValue(edge))
      setMessage("")
    },
    [canvasNodes],
  )

  function closeEdgeEditor() {
    setEditingEdgeId("")
    setEdgeDecisionDraft("")
  }

  function saveEdgeCondition() {
    if (!editingEdge) return
    setFlowEdges((current) =>
      current.map((edge) => (edge.id === editingEdge.id ? setDecisionEdgeCondition(edge, edgeDecisionDraft) : edge)),
    )
    setToast(edgeDecisionDraft ? `连线条件已设置为 ${edgeDecisionDraft}` : "连线条件已清空")
    closeEdgeEditor()
  }

  async function saveWorkflow() {
    setSaving(true)
    setMessage("")
    try {
      const action = workflowTemplateSaveAction(workflows, {
        name: workflowName,
        description: workflowDescription,
        definition,
      })
      const saved = action.type === "update"
        ? await updateWorkflow(action.workflowId, action.payload)
        : await createWorkflow(action.payload)
      onWorkflowSaved(saved)
      setToast(action.type === "update" ? workflowBuilderCopy.updatedToast : workflowBuilderCopy.createdToast)
      setMessage(`${action.type === "update" ? "已覆盖" : "已保存"}：${saved.id}`)
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={modal ? "workflow-page workflow-page-modal" : "page active workflow-page"}>
      <PageTitle
        title={workflowBuilderCopy.title}
        description={workflowBuilderCopy.description}
      >
        <button className="btn btn-primary" type="button" onClick={saveWorkflow} disabled={saving || !workflowName.trim()}>
          <Save size={16} />
          {saving ? workflowBuilderCopy.savingButton : workflowBuilderCopy.saveButton}
        </button>
      </PageTitle>

      <div className="workflow-layout">
        <section className="panel workflow-canvas-panel">
          <div className="workflow-canvas-header">
            <div className="workflow-config-fields">
              <label className="field compact-field">
                <span>Workflow 名称</span>
                <input className="input" value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="请输入 Workflow 名称" />
              </label>
              <label className="field compact-field">
                <span>描述</span>
                <input className="input" value={workflowDescription} onChange={(event) => setWorkflowDescription(event.target.value)} placeholder="请输入 Workflow 描述" />
              </label>
              <div className="workflow-config-summary" aria-label="Workflow 画布统计">
                <span>节点 {definition.nodes.length}</span>
                <span>连线 {definition.edges.length}</span>
                <small>选中节点后新增会自动连接</small>
              </div>
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
            </div>
          </div>
          <div className={resourcePanelCollapsed ? "workflow-canvas resource-collapsed" : "workflow-canvas"} aria-label="Workflow 自由画布">
            <button
              className={resourcePanelCollapsed ? "workflow-resource-toggle collapsed" : "workflow-resource-toggle"}
              type="button"
              onClick={() => setResourcePanelCollapsed((collapsed) => !collapsed)}
              aria-expanded={!resourcePanelCollapsed}
              aria-controls="workflow-resource-sidebar"
              title={workflowResourceToggleLabel(resourcePanelCollapsed)}
            >
              {resourcePanelCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
              <span>{workflowResourceToggleLabel(resourcePanelCollapsed)}</span>
            </button>
            <aside
              id="workflow-resource-sidebar"
              className={workflowResourcePanelClass(resourcePanelCollapsed)}
              aria-label="Workflow 资源侧栏"
              aria-hidden={resourcePanelCollapsed}
            >
              <div className="workflow-resource-tabs" role="tablist" aria-label="Workflow 资源类型">
                <button
                  className={activeResourcePanel === "agents" ? "workflow-resource-tab active" : "workflow-resource-tab"}
                  type="button"
                  onClick={() => setActiveResourcePanel("agents")}
                  role="tab"
                  aria-selected={activeResourcePanel === "agents"}
                >
                  <Bot size={15} />
                  可编排节点
                </button>
                <button
                  className={activeResourcePanel === "templates" ? "workflow-resource-tab active" : "workflow-resource-tab"}
                  type="button"
                  onClick={() => setActiveResourcePanel("templates")}
                  role="tab"
                  aria-selected={activeResourcePanel === "templates"}
                >
                  <ListFilter size={15} />
                  流程模板
                </button>
              </div>

              <section className="workflow-resource-panel">
                {activeResourcePanel === "agents" ? (
                  <div className="workflow-resource-content">
                    <div className="workflow-resource-toolbar">
                      <div className="workflow-resource-title">
                        <strong>可编排节点</strong>
                        <span>{availableAgents.length} 个</span>
                      </div>
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
                    </div>
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
                  </div>
                ) : (
                  <div className="workflow-resource-content">
                    <div className="workflow-resource-toolbar">
                      <div className="workflow-resource-title">
                        <strong>流程模板</strong>
                        <span>{workflows.length} 个</span>
                      </div>
                      <button className="btn btn-small workflow-new-canvas-button" type="button" onClick={loadEmptyCanvas}>
                        <Sparkles size={14} />
                        新建空白画布
                      </button>
                    </div>
                    <div className="workflow-agent-list">
                      {!workflows.length && <EmptyPanel text="暂无已保存流程模板" />}
                      {workflows.map((workflow) => {
                        const card = workflowTemplateCardView(workflow)
                        return (
                          <button
                            className={activeTemplateId === workflow.id ? "workflow-agent-card workflow-template-card active" : "workflow-agent-card workflow-template-card"}
                            key={workflow.id}
                            type="button"
                            onClick={() => loadWorkflowTemplate(workflow)}
                          >
                            <div className="workflow-agent-head">
                              <span className="workflow-agent-name">
                                <span className="workflow-icon violet"><GitBranch size={16} /></span>
                                <span>{card.title}</span>
                              </span>
                              <span className="tag workflow-agent-status-tag active">{card.statusLabel}</span>
                            </div>
                            <p>{card.description}</p>
                            <div className="tag-row">
                              <span className="tag">{card.nodeCountLabel}</span>
                              <span className="tag">{card.edgeCountLabel}</span>
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
              </section>
            </aside>
            <div className="workflow-flow-area">
              <ReactFlow
                nodes={visibleFlowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onEdgeClick={handleEdgeClick}
                onNodeClick={(_event, node) => setActiveNodeId(node.id)}
                onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
                onNodeMouseLeave={() => setHoveredNodeId("")}
                defaultViewport={{ x: 24, y: 28, zoom: 0.9 }}
                fitView
                fitViewOptions={{ padding: 0.18, maxZoom: 0.95 }}
                minZoom={0.35}
                maxZoom={1.8}
              >
                <Background gap={18} />
                <MiniMap pannable zoomable />
                <Controls />
              </ReactFlow>
              {editingEdge && (
                <div className="workflow-edge-editor-backdrop" onMouseDown={closeEdgeEditor}>
                  <section className="workflow-edge-editor" aria-label="编辑连线条件" onMouseDown={(event) => event.stopPropagation()}>
                    <header>
                      <strong>编辑连线条件</strong>
                      <span>{nodeTitle(canvasNodes, editingEdge.source)}{" -> "}{nodeTitle(canvasNodes, editingEdge.target)}</span>
                    </header>
                    <label className="field compact-field">
                      <span>decision 值</span>
                      <select className="input" value={edgeDecisionDraft} onChange={(event) => setEdgeDecisionDraft(event.target.value)}>
                        <option value="">无条件</option>
                        {edgeDecisionOptions.map((decision) => (
                          <option key={decision} value={decision}>
                            {decision}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="workflow-edge-editor-actions">
                      <button className="btn btn-small" type="button" onClick={closeEdgeEditor}>
                        取消
                      </button>
                      <button className="btn btn-small btn-primary" type="button" onClick={saveEdgeCondition}>
                        保存条件
                      </button>
                    </div>
                  </section>
                </div>
              )}
            </div>
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

function PageTitle({ title, description, children }: { title: string; description: string; children: ReactNode }) {
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
          <span className="workflow-node-head">
            <span className="workflow-icon violet">{nodeIcon(String(data.kind), data.id)}</span>
            <span className="workflow-node-title-group">
              <strong>{data.title || data.id}</strong>
              <span className="workflow-node-kind">{nodeKindText(String(data.kind))}</span>
            </span>
          </span>
          <small>{data.conditionDescription || "decision"}</small>
        </span>
      ) : (
        <>
          <span className="workflow-node-head">
            <span className={data.kind === "human" ? "workflow-icon warning" : data.kind === "end" ? "workflow-icon success" : "workflow-icon"}>
              {nodeIcon(String(data.kind), data.id)}
            </span>
            <span className="workflow-node-title-group">
              <strong>{data.title || data.id}</strong>
              <span className="workflow-node-kind">{nodeKindText(String(data.kind))}</span>
            </span>
          </span>
          <small>{data.description || "-"}</small>
          {data.kind === "human" && (data.assigneeUserName || data.assignee) && <small className="workflow-node-instruction">人员：{data.assigneeUserName || data.assignee}</small>}
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
              {field.inputType === "condition_options" ? (
                <WorkflowConditionOptionsInput
                  options={field.conditionOptions || []}
                  onChange={(options) => data.onConfigChange?.(data.id, { [field.key]: options })}
                />
              ) : field.inputType === "textarea" ? (
                <WorkflowInlineTextInput
                  as="textarea"
                  field={field}
                  onChange={(value) => data.onConfigChange?.(data.id, { [field.key]: value })}
                />
              ) : field.inputType === "user_select" ? (
                <select
                  value={field.value}
                  onKeyDown={(event) => event.stopPropagation()}
                  onChange={(event) => data.onConfigChange?.(data.id, { [field.key]: event.target.value })}
                >
                  <option value="">{field.placeholder}</option>
                  {(data.userOptions || []).map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.name}
                    </option>
                  ))}
                </select>
              ) : (
                <WorkflowInlineTextInput
                  as="input"
                  field={field}
                  onChange={(value) => data.onConfigChange?.(data.id, { [field.key]: value })}
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

function WorkflowInlineTextInput({
  as,
  field,
  onChange,
}: {
  as: "input" | "textarea"
  field: { value: string; placeholder: string }
  onChange: (value: string) => void
}) {
  const [draft, setDraft] = useState({ value: field.value, composing: false })

  useEffect(() => {
    setDraft((current) => reduceWorkflowInlineTextDraft(current, { type: "external_value", value: field.value }).state)
  }, [field.value])

  function applyDraft(action: Parameters<typeof reduceWorkflowInlineTextDraft>[1]) {
    setDraft((current) => {
      const result = reduceWorkflowInlineTextDraft(current, action)
      if (result.commitValue !== undefined) onChange(result.commitValue)
      return result.state
    })
  }

  const commonProps = {
    value: draft.value,
    placeholder: field.placeholder,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      applyDraft({
        type: "change",
        value: event.target.value,
        isComposing: isComposingNativeEvent(event.nativeEvent),
      }),
    onCompositionStart: () => applyDraft({ type: "composition_start" }),
    onCompositionEnd: (event: React.CompositionEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      applyDraft({ type: "composition_end", value: event.currentTarget.value }),
    onBlur: (event: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      applyDraft({ type: "blur", value: event.currentTarget.value }),
    onKeyDown: (event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => event.stopPropagation(),
  }

  return as === "textarea" ? <textarea {...commonProps} /> : <input {...commonProps} />
}

function isComposingNativeEvent(event: Event) {
  return Boolean((event as Event & { isComposing?: boolean }).isComposing)
}

function WorkflowConditionOptionsInput({
  options,
  onChange,
}: {
  options: Array<{ value: string; content: string }>
  onChange: (options: Array<{ value: string; content: string }>) => void
}) {
  const rows = normalizeWorkflowConditionOptions(options)
  const editableRows = rows.length ? rows : [{ value: "", content: "" }]

  function updateRow(index: number, patch: Partial<{ value: string; content: string }>) {
    const nextRows = editableRows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row))
    onChange(nextRows)
  }

  function addRow() {
    onChange([...editableRows, { value: "", content: "" }])
  }

  function removeRow(index: number) {
    const nextRows = editableRows.filter((_row, rowIndex) => rowIndex !== index)
    onChange(nextRows.length ? nextRows : [{ value: "", content: "" }])
  }

  return (
    <div className="workflow-condition-options-editor">
      {editableRows.map((row, index) => (
        <div className="workflow-condition-option-row" key={`condition-option-${index}`}>
          <div className="workflow-condition-option-head">
            <label>
              <span>分支值</span>
              <WorkflowInlineTextInput
                as="input"
                field={{ value: row.value, placeholder: "请输入分支值" }}
                onChange={(value) => updateRow(index, { value })}
              />
            </label>
            <button className="btn btn-small btn-danger" type="button" onClick={() => removeRow(index)}>
              删除
            </button>
          </div>
          <WorkflowInlineTextInput
            as="textarea"
            field={{ value: row.content, placeholder: "请输入判断说明" }}
            onChange={(content) => updateRow(index, { content })}
          />
        </div>
      ))}
      <button className="btn btn-small" type="button" onClick={addRow}>
        <Plus size={14} />
        添加分支
      </button>
    </div>
  )
}

function edgeDecisionValue(edge: WorkflowReactFlowEdge | null) {
  const condition = edge?.data?.condition
  if (!condition || typeof condition !== "object") return ""
  const value = (condition as Record<string, unknown>).value
  return typeof value === "string" ? value : ""
}

function decisionOptionsForEdge(nodes: WorkflowNode[], edge: WorkflowReactFlowEdge | null) {
  const sourceNode = edge ? nodes.find((node) => node.id === edge.source) : null
  return workflowConditionDecisionValues(sourceNode)
}

function nodeTitle(nodes: WorkflowNode[], nodeId: string) {
  const node = nodes.find((item) => item.id === nodeId)
  return node?.title || nodeId
}

function normalizeNodePatch(patch: Record<string, unknown>, users: UserOption[]): Record<string, unknown> {
  if (patch.assignee_user_id === undefined) return patch
  const userId = String(patch.assignee_user_id || "")
  const user = users.find((item) => item.id === userId)
  return {
    ...patch,
    assignee_user_name: user?.name || "",
    assignee_role: user?.role || "",
  }
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
