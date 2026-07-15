import {
  Activity,
  Bot,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  ListChecks,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react"
import { FormEvent, useEffect, useMemo, useState } from "react"
import {
  Agent,
  SimpleAgentResponse,
  SubTask,
  Task,
  TaskRound,
  cancelTask,
  confirmTask,
  createSimpleAgent,
  createTaskRequest,
  getTask,
  listAgents,
  listHumanSubtasks,
  listTasks,
  submitHumanSubtaskResult,
} from "./api/taskhub"

type PageId = "overview" | "publish" | "confirmation" | "tasks" | "agents" | "audit" | "governance"

const navGroups = [
  { label: "工作总览", items: [{ id: "overview", text: "协同总览", icon: Activity }] },
  {
    label: "发布与确认",
    items: [
      { id: "publish", text: "任务发布", icon: Send },
      { id: "confirmation", text: "人工确认", icon: ClipboardCheck },
    ],
  },
  { label: "任务中心", items: [{ id: "tasks", text: "任务列表", icon: ListChecks }] },
  {
    label: "管理治理",
    items: [
      { id: "agents", text: "Agent 管理", icon: Bot },
      { id: "audit", text: "审计记录", icon: FileText },
      { id: "governance", text: "平台治理", icon: ShieldCheck },
    ],
  },
] as const

function statusText(status?: string) {
  const value = status || "running"
  return { running: "正在执行", succeeded: "执行完成", failed: "执行失败" }[value] || value
}

function taskTitle(task: Task) {
  return task.title || task.draft?.title || task.content || task.id
}

function taskDescription(task: Task) {
  return task.description || task.draft?.description || task.content || "-"
}

function taskStatus(task: Task) {
  return task.task_status || task.status || "running"
}

function formatDate(value?: string) {
  if (!value) return "-"
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value))
}

export default function App() {
  const [page, setPage] = useState<PageId>("overview")
  const [tasks, setTasks] = useState<Task[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [humanSubtasks, setHumanSubtasks] = useState<SubTask[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string>("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [toast, setToast] = useState("")

  const events = useMemo(
    () =>
      tasks
        .flatMap((task) => (task.events || []).map((event) => ({ ...event, task_title: taskTitle(task), task_id: task.id })))
        .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at))),
    [tasks],
  )

  async function refreshAll() {
    setLoading(true)
    setError("")
    try {
      const [nextTasks, nextAgents, nextHumanSubtasks] = await Promise.all([listTasks(), listAgents(), listHumanSubtasks()])
      setTasks(nextTasks || [])
      setAgents(nextAgents || [])
      setHumanSubtasks(nextHumanSubtasks || [])
      if (!selectedTaskId && nextTasks?.[0]) setSelectedTaskId(nextTasks[0].id)
    } catch (err) {
      setError(err instanceof Error ? err.message : "刷新失败")
    } finally {
      setLoading(false)
    }
  }

  async function refreshTaskList() {
    setLoading(true)
    setError("")
    try {
      const nextTasks = await listTasks()
      setTasks(nextTasks || [])
      if (!selectedTaskId && nextTasks?.[0]) setSelectedTaskId(nextTasks[0].id)
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务列表刷新失败")
    } finally {
      setLoading(false)
    }
  }

  async function navigateTo(nextPage: PageId) {
    setPage(nextPage)
    if (nextPage === "tasks") {
      await refreshTaskList()
    }
  }

  useEffect(() => {
    void refreshAll()
  }, [])

  return (
    <div className="app-shell">
      <aside className="side-nav">
        <div className="brand">
          <h1 className="brand-title">TaskHub</h1>
          <div className="brand-subtitle">Agent 任务协同中心</div>
        </div>
        <div className="nav-section">
          {navGroups.map((group) => (
            <div key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item, index) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    className="nav-button"
                    aria-current={page === item.id ? "page" : undefined}
                    onClick={() => void navigateTo(item.id as PageId)}
                  >
                    <span className="nav-text">
                      <Icon size={16} />
                      {item.text}
                    </span>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      </aside>

      <main className="main">
        <header className="top-toolbar">
          <div className="toolbar-left">
            <div className="global-search">
              <Search size={16} />
              <span>搜索任务、Agent、执行节点</span>
            </div>
          </div>
          <div className="toolbar-right">
            <span className={error ? "status-pill danger" : "status-pill success"}>{error ? "接口异常" : "接口联调模式"}</span>
            <button className="btn" onClick={refreshAll} disabled={loading}>
              {loading ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
              刷新
            </button>
          </div>
        </header>

        <section className="content">
          {toast && <div className="toast">{toast}</div>}
          {error && <div className="alert danger">{error}</div>}
          {page === "overview" && <Overview tasks={tasks} agents={agents} humanSubtasks={humanSubtasks} events={events} setPage={(nextPage) => void navigateTo(nextPage)} />}
          {page === "publish" && <PublishPage onCreated={(created) => {
            setTasks((current) => mergeTasks(current, created))
            setSelectedTaskId(created[0]?.id || "")
            setToast("已识别任务清单，请在弹窗中确认后执行")
          }} onConfirmed={(confirmed) => {
            setTasks((current) => mergeTasks(current, confirmed))
            setSelectedTaskId(confirmed[0]?.id || selectedTaskId)
            setToast("任务已确认，系统正在异步执行")
          }} onCancelled={(cancelledIds) => {
            setTasks((current) => current.filter((task) => !cancelledIds.includes(task.id)))
            if (cancelledIds.includes(selectedTaskId)) setSelectedTaskId("")
            setToast("任务清单已取消")
          }} />}
          {page === "confirmation" && <ConfirmationPage humanSubtasks={humanSubtasks} refreshAll={refreshAll} />}
          {page === "tasks" && <TasksPage tasks={tasks} setSelectedTaskId={setSelectedTaskId} onOpenHumanWorkbench={async () => {
            await refreshAll()
            setPage("confirmation")
          }} />}
          {page === "agents" && <AgentsPage agents={agents} setAgents={setAgents} setToast={setToast} />}
          {page === "audit" && <AuditPage events={events} />}
          {page === "governance" && <GovernancePage />}
        </section>
      </main>
    </div>
  )
}

function mergeTasks(current: Task[], incoming: Task[]) {
  const incomingIds = new Set(incoming.map((task) => task.id))
  return [...incoming, ...current.filter((task) => !incomingIds.has(task.id))]
}

function Overview({
  tasks,
  agents,
  humanSubtasks,
  events,
  setPage,
}: {
  tasks: Task[]
  agents: Agent[]
  humanSubtasks: SubTask[]
  events: Array<Record<string, unknown>>
  setPage: (page: PageId) => void
}) {
  const running = tasks.filter((task) => taskStatus(task) === "running").length
  const succeeded = tasks.filter((task) => taskStatus(task) === "succeeded").length
  const failed = tasks.filter((task) => taskStatus(task) === "failed").length
  return (
    <div className="page active">
      <PageHeader title="协同运营驾驶舱" description="聚合任务运行信号、节点负载、Agent 覆盖和异常收敛状态。">
        <button className="btn btn-primary" onClick={() => setPage("publish")}>
          <Plus size={16} />
          发布任务
        </button>
      </PageHeader>
      <div className="metric-grid">
        <Metric label="运行中任务" value={running} tone="info" />
        <Metric label="执行完成" value={succeeded} tone="success" />
        <Metric label="执行失败" value={failed} tone="danger" />
        <Metric label="人工待处理" value={humanSubtasks.length} tone="warning" />
        <Metric label="处理 Agent" value={agents.filter((agent) => agent.agent_type !== "condition").length} tone="info" />
      </div>
      <div className="grid two">
        <Panel title="节点态势">
          {["请求接入", "人工确认", "分发执行", "上下文沉淀", "闭环判断"].map((item, index) => (
            <div className="flow-row" key={item}>
              <span className="flow-dot">{index + 1}</span>
              <span>{item}</span>
              <span className="muted">{index < 3 ? "活跃" : "稳定"}</span>
            </div>
          ))}
        </Panel>
        <Panel title="最近任务">
          <TaskTable tasks={tasks.slice(0, 5)} compact />
        </Panel>
      </div>
      <Panel title="最近事件">
        <EventList events={events.slice(0, 6)} />
      </Panel>
    </div>
  )
}

function PublishPage({
  onCreated,
  onConfirmed,
  onCancelled,
}: {
  onCreated: (tasks: Task[]) => void
  onConfirmed: (tasks: Task[]) => void
  onCancelled: (taskIds: string[]) => void
}) {
  const [content, setContent] = useState("请分析客户需求，生成一份报告并保存到本地目录")
  const [draftTasks, setDraftTasks] = useState<Task[]>([])
  const [intentModalOpen, setIntentModalOpen] = useState(false)
  const [intentError, setIntentError] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [message, setMessage] = useState("")

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setMessage("")
    setIntentError("")
    setDraftTasks([])
    setIntentModalOpen(true)
    try {
      const response = await createTaskRequest(content)
      onCreated(response.tasks || [])
      setDraftTasks(response.tasks || [])
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "提交失败"
      setIntentError(errorMessage)
      setMessage(errorMessage)
    } finally {
      setSubmitting(false)
    }
  }

  async function confirmDrafts() {
    setConfirming(true)
    setMessage("")
    try {
      const confirmed = []
      for (const task of draftTasks) {
        confirmed.push(
          await confirmTask(task.id, {
            title: taskTitle(task),
            description: taskDescription(task),
            execution_mode: "async",
          }),
        )
      }
      onConfirmed(confirmed)
      setDraftTasks([])
      setIntentModalOpen(false)
      setMessage("任务已确认，系统正在异步执行")
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "确认失败")
    } finally {
      setConfirming(false)
    }
  }

  function updateDraftTask(taskId: string, patch: Partial<Pick<Task, "title" | "description">>) {
    setDraftTasks((current) => current.map((task) => (task.id === taskId ? { ...task, ...patch } : task)))
  }

  async function closeIntentModal() {
    if (submitting || confirming) return
    const taskIds = draftTasks.map((task) => task.id)
    if (taskIds.length > 0) {
      setConfirming(true)
      setMessage("")
      try {
        await Promise.all(taskIds.map((taskId) => cancelTask(taskId)))
        onCancelled(taskIds)
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : "取消任务失败"
        setIntentError(errorMessage)
        setMessage(errorMessage)
        return
      } finally {
        setConfirming(false)
      }
    }
    setDraftTasks([])
    setIntentError("")
    setIntentModalOpen(false)
  }

  return (
    <div className="page active">
      <PageHeader title="任务发布页" description="将自然语言请求提交为可追踪的 Request。" />
      <form className="form-panel" onSubmit={submit}>
        <label className="field">
          <span>任务诉求</span>
          <textarea className="textarea" value={content} onChange={(event) => setContent(event.target.value)} />
        </label>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={submitting || !content.trim()}>
            {submitting ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
            提交请求
          </button>
          {message && <span className="form-message danger-text">{message}</span>}
        </div>
      </form>
      {intentModalOpen && (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-panel intent-modal" role="dialog" aria-modal="true" aria-label="意图识别任务清单">
            <header className="modal-header">
              <div>
                <h3>意图识别任务清单</h3>
                <p>{submitting ? "正在拆分整理任务清单，请稍后" : "请确认识别出的任务名称和描述，确认后系统会异步执行。"}</p>
              </div>
              <button className="btn" onClick={() => void closeIntentModal()} disabled={submitting || confirming}>关闭</button>
            </header>
            {submitting ? (
              <div className="intent-loading">
                <Loader2 size={34} className="spin" />
                <strong>正在拆分整理任务清单，请稍后</strong>
                <span>系统正在调用意图识别能力，返回后会在这里展示待确认任务。</span>
              </div>
            ) : intentError ? (
              <div className="intent-loading error">
                <XCircle size={34} />
                <strong>任务清单整理失败</strong>
                <span>{intentError}</span>
              </div>
            ) : (
              <>
                <div className="intent-task-list">
                  {draftTasks.map((task, index) => (
                    <div className="intent-task-card" key={task.id}>
                      <div className="intent-task-index">任务 {index + 1}</div>
                      <label className="field">
                        <span>任务名称</span>
                        <input className="input" value={taskTitle(task)} onChange={(event) => updateDraftTask(task.id, { title: event.target.value })} />
                      </label>
                      <label className="field">
                        <span>任务描述</span>
                        <textarea className="textarea" value={taskDescription(task)} onChange={(event) => updateDraftTask(task.id, { description: event.target.value })} />
                      </label>
                    </div>
                  ))}
                </div>
                <div className="modal-actions">
                  <button className="btn" onClick={() => void closeIntentModal()} disabled={confirming}>取消</button>
                  <button className="btn btn-primary" onClick={confirmDrafts} disabled={confirming || draftTasks.length === 0}>
                    {confirming ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
                    确认并执行
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

function ConfirmationPage({
  humanSubtasks,
  refreshAll,
}: {
  humanSubtasks: SubTask[]
  refreshAll: () => Promise<void>
}) {
  const [activeId, setActiveId] = useState("")
  const active = humanSubtasks.find((subtask) => subtask.id === activeId) || humanSubtasks[0]
  const [opinion, setOpinion] = useState("")
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (active) {
      setActiveId(active.id)
      setOpinion(active.output || "")
    }
  }, [active?.id])

  async function submit(decision: "approved" | "rejected") {
    if (!active) return
    setSubmitting(true)
    try {
      await submitHumanSubtaskResult(active.id, {
        result_status: "succeeded",
        output: opinion.trim() || (decision === "approved" ? "人工确认通过" : "人工驳回"),
        should_complete: true,
        metadata: { decision },
        execution_mode: "async",
      })
      await refreshAll()
      setOpinion("")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page active">
      <PageHeader title="人工节点工作台" description="处理任务流转中的人工节点，提交通过或驳回结果。" />
      {!active ? (
        <EmptyState text="暂无待处理人工节点" />
      ) : (
        <div className="grid two">
          <Panel title="人工节点队列">
            {humanSubtasks.map((subtask) => (
              <button key={subtask.id} className={subtask.id === active.id ? "list-item active" : "list-item"} onClick={() => setActiveId(subtask.id)}>
                <span>{subtask.title || subtask.description || subtask.id}</span>
                <small>{subtask.current_node || subtask.assignee_type || "human"}</small>
              </button>
            ))}
          </Panel>
          <Panel title="人工处理">
            <div className="human-subtask-detail">
              <div>
                <span className="muted">子任务名称</span>
                <strong>{active.title || active.id}</strong>
              </div>
              <div>
                <span className="muted">子任务描述</span>
                <p>{active.description || "-"}</p>
              </div>
              <div className="detail-grid">
                <span className={`status-pill ${active.status || "running"}`}>{statusText(active.status)}</span>
                <span className="muted">任务 ID：{active.task_id || "-"}</span>
                <span className="muted">处理节点：{active.current_node || "human"}</span>
              </div>
            </div>
            <label className="field">
              <span>处理意见</span>
              <textarea className="textarea" value={opinion} onChange={(event) => setOpinion(event.target.value)} placeholder="填写人工判断、补充信息或驳回原因" />
            </label>
            <div className="form-actions">
              <button className="btn btn-primary" onClick={() => void submit("approved")} disabled={submitting}>
                {submitting ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
                确认通过
              </button>
              <button className="btn btn-danger" onClick={() => void submit("rejected")} disabled={submitting}>
                <XCircle size={16} />
                驳回
              </button>
            </div>
          </Panel>
        </div>
      )}
    </div>
  )
}

function TasksPage({
  tasks,
  setSelectedTaskId,
  onOpenHumanWorkbench,
}: {
  tasks: Task[]
  setSelectedTaskId: (id: string) => void
  onOpenHumanWorkbench: () => Promise<void>
}) {
  const [detailTask, setDetailTask] = useState<Task | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState("")

  async function openDetail(taskId: string) {
    setSelectedTaskId(taskId)
    setDetailLoading(true)
    setDetailError("")
    setDetailTask(tasks.find((task) => task.id === taskId) || null)
    try {
      setDetailTask(await getTask(taskId))
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "任务详情加载失败")
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    if (!detailTask || taskStatus(detailTask) !== "running") return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const latest = await getTask(detailTask.id)
        if (!cancelled) {
          setDetailTask(latest)
          setDetailError("")
        }
      } catch (err) {
        if (!cancelled) setDetailError(err instanceof Error ? err.message : "任务详情刷新失败")
      }
    }, 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [detailTask?.id, detailTask?.task_status, detailTask?.status])

  return (
    <div className="page active">
      <PageHeader title="任务列表" description="按状态、节点、来源和执行者定位任务，点击详情查看执行轨迹。" />
      <Panel title="任务表">
        <TaskTable tasks={tasks} onSelect={openDetail} selectedTaskId={detailTask?.id} />
      </Panel>
      {detailTask && (
        <TaskDetailModal
          task={detailTask}
          loading={detailLoading}
          error={detailError}
          polling={taskStatus(detailTask) === "running"}
          onOpenHumanWorkbench={() => {
            setDetailTask(null)
            setDetailError("")
            void onOpenHumanWorkbench()
          }}
          onClose={() => {
            setDetailTask(null)
            setDetailError("")
          }}
        />
      )}
    </div>
  )
}

function TaskDetailModal({
  task,
  loading,
  error,
  polling,
  onOpenHumanWorkbench,
  onClose,
}: {
  task: Task
  loading: boolean
  error: string
  polling: boolean
  onOpenHumanWorkbench: () => void
  onClose: () => void
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal-panel task-detail-modal" role="dialog" aria-modal="true" aria-label="任务详情" onMouseDown={(event) => event.stopPropagation()}>
        <header className="modal-header">
          <div>
            <h3>{taskTitle(task)}</h3>
            <p>{task.description || task.content || task.id}</p>
          </div>
          <button className="btn" onClick={onClose}>关闭</button>
        </header>
        <div className="task-detail-body">
          <div className="task-detail-summary">
            {loading && <div className="alert"><Loader2 size={16} className="spin" /> 正在加载最新详情</div>}
            {polling && !loading && <div className="alert info"><Loader2 size={16} className="spin" /> 任务执行中，详情每 3 秒自动刷新</div>}
            {error && <div className="alert danger">{error}</div>}
            <div className="detail-grid">
              <span className={`status-pill ${taskStatus(task)}`}>{statusText(taskStatus(task))}</span>
              <span className="muted">当前节点：{task.current_node || "-"}</span>
              <span className="muted">循环轮次：{task.loop_count ?? 0}/{task.max_loop_count ?? 10}</span>
            </div>
            <div className="context-box">{task.context?.summary || task.final_output || "暂无上下文摘要"}</div>
          </div>
          <section className="execution-section">
            <h4>执行轮次</h4>
            {(task.context?.rounds || []).length ? (
              <div className="modal-scroll execution-scroll">
                <ExecutionGraph rounds={task.context?.rounds || []} onOpenHumanWorkbench={onOpenHumanWorkbench} />
              </div>
            ) : (
              <EmptyState text="暂无执行轮次" />
            )}
          </section>
        </div>
      </section>
    </div>
  )
}

function ExecutionGraph({ rounds, onOpenHumanWorkbench }: { rounds: TaskRound[]; onOpenHumanWorkbench: () => void }) {
  return (
    <div className="execution-graph" aria-label="任务执行有向图">
      <div className="graph-node graph-terminal">
        <span className="graph-terminal-dot" />
        任务开始
      </div>
      {rounds.map((round) => (
        <div className="graph-step" key={round.id || round.round_index}>
          <div className="graph-arrow" aria-hidden="true" />
          <section className="graph-round-node">
            <header className="graph-round-header">
              <div>
                <strong>第 {round.round_index ?? "-"} 轮</strong>
                <span>{round.execution_mode || "unknown"}</span>
              </div>
              <span className="status-pill info">{round.subtasks?.length || 0} 个子任务</span>
            </header>
            {round.reason && (
              <details className="round-reason">
                <summary>分发说明</summary>
                <p>{round.reason}</p>
              </details>
            )}
            <div className={round.execution_mode === "parallel" ? "graph-subtasks parallel" : "graph-subtasks sequential"}>
              {(round.subtasks || []).map((subtask) => {
                const isHumanNode = subtask.assignee_type === "human" || subtask.current_node === "human"
                const canOpenHumanWorkbench = isHumanNode && (subtask.status || "running") === "running"
                const nodeContent = (
                  <>
                    <div className="subtask-node-title">{subtask.title || subtask.id}</div>
                    <div className="subtask-node-meta">
                      <span>{isHumanNode ? "人工节点" : "Agent节点"}</span>
                      <span className={`status-pill ${subtask.status}`}>{statusText(subtask.status)}</span>
                    </div>
                    {canOpenHumanWorkbench && <span className="subtask-node-action">点击处理</span>}
                  </>
                )
                return canOpenHumanWorkbench ? (
                  <button type="button" className={`graph-subtask-node clickable ${subtask.status || "running"}`} key={subtask.id} onClick={onOpenHumanWorkbench}>
                    {nodeContent}
                  </button>
                ) : (
                  <article className={`graph-subtask-node ${subtask.status || "running"}`} key={subtask.id}>
                    {nodeContent}
                  </article>
                )
              })}
            </div>
          </section>
        </div>
      ))}
      <div className="graph-step">
        <div className="graph-arrow" aria-hidden="true" />
        <div className="graph-node graph-terminal">
          <CheckCircle2 size={16} />
          任务闭环
        </div>
      </div>
    </div>
  )
}

function AgentsPage({ agents, setAgents, setToast }: { agents: Agent[]; setAgents: (agents: Agent[] | ((current: Agent[]) => Agent[])) => void; setToast: (value: string) => void }) {
  const [ability, setAbility] = useState("向指定目录写入文章或者报告总结")
  const [name, setName] = useState("报告写入助手")
  const [result, setResult] = useState<SimpleAgentResponse | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      const response = await createSimpleAgent(ability, name)
      setResult(response)
      if (response.agent) {
        setAgents((current) => [response.agent!, ...current])
        setToast("Agent 已创建")
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page active">
      <PageHeader title="Agent 管理" description="通过一句诉求创建具体能力 Agent，并查看能力标签和工具定义。" />
      <div className="grid two">
        <form className="form-panel" onSubmit={submit}>
          <label className="field">
            <span>Agent 名称</span>
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="field">
            <span>能力诉求</span>
            <textarea className="textarea" value={ability} onChange={(event) => setAbility(event.target.value)} />
          </label>
          <button className="btn btn-primary" disabled={submitting || !ability.trim()}>
            {submitting ? <Loader2 size={16} className="spin" /> : <Bot size={16} />}
            极简创建 Agent
          </button>
          {result && (
            <div className={`alert ${result.status === "created" ? "success" : "warning"}`}>
              <strong>{result.status}</strong>
              <p>{result.message}</p>
              {result.guidance?.map((item) => <p key={item}>{item}</p>)}
            </div>
          )}
        </form>
        <Panel title="已注册 Agent">
          <div className="agent-list">
            {agents.map((agent) => (
              <div className="agent-card" key={agent.id}>
                <div>
                  <strong>{agent.name}</strong>
                  <p>{agent.description}</p>
                </div>
                <div className="tag-row">
                  {(agent.capabilities || []).slice(0, 4).map((capability) => <span className="tag" key={capability}>{capability}</span>)}
                </div>
                <small>{(agent.tools || []).map((tool) => tool.type).join("、") || "无工具"}</small>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}

function AuditPage({ events }: { events: Array<Record<string, unknown>> }) {
  return (
    <div className="page active">
      <PageHeader title="审计记录" description="从任务事件中回放确认、分发、执行和闭环过程。" />
      <Panel title="事件流">
        <EventList events={events} />
      </Panel>
    </div>
  )
}

function GovernancePage() {
  return (
    <div className="page active">
      <PageHeader title="平台治理" description="规则、来源权限和紧急开关的产品 1.0 预留页面。" />
      <div className="grid three">
        <Metric label="运行模式" value="接口联调" tone="info" />
        <Metric label="循环超限" value="10轮转人工" tone="warning" />
        <Metric label="权限矩阵" value="预留" tone="info" />
      </div>
    </div>
  )
}

function PageHeader({ title, description, children }: { title: string; description: string; children?: React.ReactNode }) {
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

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-title">{title}</div>
      {children}
    </section>
  )
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function TaskTable({ tasks, compact, onSelect, selectedTaskId }: { tasks: Task[]; compact?: boolean; onSelect?: (id: string) => void; selectedTaskId?: string }) {
  const [page, setPage] = useState(1)
  const [tooltip, setTooltip] = useState<{ text: string; x: number; y: number } | null>(null)
  const sortedTasks = useMemo(
    () =>
      tasks
        .slice()
        .sort((left, right) => new Date(right.created_at || 0).getTime() - new Date(left.created_at || 0).getTime()),
    [tasks],
  )
  const pageSize = compact ? sortedTasks.length || 1 : 20
  const totalPages = compact ? 1 : Math.max(1, Math.min(10, Math.ceil(sortedTasks.length / pageSize)))
  const visibleTasks = compact ? sortedTasks : sortedTasks.slice((Math.min(page, totalPages) - 1) * pageSize, Math.min(page, totalPages) * pageSize)

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  if (!tasks.length) return <EmptyState text="暂无任务" />
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务名称</th>
              {!compact && <th className="description-col">任务描述</th>}
              {!compact && <th>节点</th>}
              <th>状态</th>
              <th>创建时间</th>
              {onSelect && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {visibleTasks.map((task) => (
              <tr key={task.id} className={selectedTaskId === task.id ? "selected" : ""}>
                <td className="task-name-cell">
                  <span
                    className="task-name-ellipsis"
                    onMouseEnter={(event) => setTooltip({ text: taskTitle(task), x: event.clientX, y: event.clientY })}
                    onMouseMove={(event) => setTooltip((current) => current ? { ...current, x: event.clientX, y: event.clientY } : null)}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    {taskTitle(task)}
                  </span>
                </td>
                {!compact && (
                  <td className="description-col">
                    <span
                      className="description-ellipsis"
                      onMouseEnter={(event) => setTooltip({ text: taskDescription(task), x: event.clientX, y: event.clientY })}
                      onMouseMove={(event) => setTooltip((current) => current ? { ...current, x: event.clientX, y: event.clientY } : null)}
                      onMouseLeave={() => setTooltip(null)}
                    >
                      {taskDescription(task)}
                    </span>
                  </td>
                )}
                {!compact && <td>{task.current_node || "-"}</td>}
                <td><span className={`status-pill ${taskStatus(task)}`}>{statusText(taskStatus(task))}</span></td>
                <td>{formatDate(task.created_at)}</td>
                {onSelect && <td><button className="btn btn-small" onClick={() => onSelect(task.id)}>详情</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!compact && (
        <div className="pagination">
          <span className="muted">第 {Math.min(page, totalPages)} / {totalPages} 页，共展示前 {Math.min(sortedTasks.length, 200)} 条，每页 20 条</span>
          <div className="pagination-buttons">
            <button className="btn btn-small" disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button>
            {Array.from({ length: totalPages }, (_, index) => index + 1).map((item) => (
              <button key={item} className={item === page ? "btn btn-small page-active" : "btn btn-small"} onClick={() => setPage(item)}>
                {item}
              </button>
            ))}
            <button className="btn btn-small" disabled={page === totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>下一页</button>
          </div>
        </div>
      )}
      {tooltip && (
        <div className="description-tooltip" style={{ left: tooltip.x + 12, top: tooltip.y + 12 }}>
          {tooltip.text}
        </div>
      )}
    </>
  )
}

function EventList({ events }: { events: Array<Record<string, unknown>> }) {
  if (!events.length) return <EmptyState text="暂无事件" />
  return (
    <div className="event-list">
      {events.map((event, index) => (
        <div className="event-item" key={`${event.id || index}`}>
          <span className="event-dot" />
          <div>
            <strong>{String(event.event_type || event.message || "事件")}</strong>
            <p>{String(event.task_title || event.task_id || "")}</p>
          </div>
          <small>{formatDate(String(event.created_at || ""))}</small>
        </div>
      ))}
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <UserCheck size={20} />
      {text}
    </div>
  )
}
