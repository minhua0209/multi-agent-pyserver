import {
  Alert as AntAlert,
  Button,
  Card,
  ConfigProvider,
  Descriptions,
  Empty as AntEmpty,
  Flex,
  Input,
  Layout,
  List,
  Menu,
  Modal,
  Pagination,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd"
import type { ColumnsType } from "antd/es/table"
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
import { draftDescriptionValue, draftTitleValue, taskLabel } from "./intentDrafts"

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

function statusColor(status?: string) {
  const value = status || "running"
  return { running: "processing", succeeded: "success", failed: "error" }[value] || "default"
}

function toneColor(tone: string) {
  return { info: "#2563eb", success: "#16a34a", warning: "#d97706", danger: "#dc2626" }[tone] || "#2563eb"
}

function taskTitle(task: Task) {
  return task.title || task.draft?.title || task.content || task.id
}

function taskDescription(task: Task) {
  return task.description || task.draft?.description || task.content || "-"
}

function draftTaskListText(task: Task) {
  if (!task.draft) return "暂无识别任务清单"
  const title = task.draft.title || "未命名任务"
  const description = task.draft.description || ""
  return description ? `${title}\n${description}` : title
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

  const menuItems = navGroups.flatMap((group) => [
    {
      type: "group" as const,
      label: group.label,
      children: group.items.map((item) => {
        const Icon = item.icon
        return { key: item.id, label: item.text, icon: <Icon size={16} /> }
      }),
    },
  ])

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#4f46e5",
          colorInfo: "#0891b2",
          colorSuccess: "#16a34a",
          colorWarning: "#f59e0b",
          colorError: "#e11d48",
          borderRadius: 8,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Layout: { bodyBg: "#eef2ff", siderBg: "#111827", headerBg: "rgba(255,255,255,0.9)" },
          Card: { borderRadiusLG: 8 },
          Menu: { darkItemBg: "#111827", darkSubMenuItemBg: "#111827", darkItemSelectedBg: "#4338ca" },
        },
      }}
    >
      <Layout className="app-shell antd-shell">
        <Layout.Sider width={252} className="side-nav antd-sider">
          <div className="brand">
            <h1 className="brand-title">TaskHub</h1>
            <div className="brand-subtitle">Agent 任务协同中心</div>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[page]}
            items={menuItems}
            className="app-menu"
            onClick={(item) => void navigateTo(item.key as PageId)}
          />
        </Layout.Sider>

        <Layout>
          <Layout.Header className="top-toolbar">
            <Input
              className="global-search"
              prefix={<Search size={16} />}
              placeholder="搜索任务、Agent、执行节点"
              readOnly
            />
            <Flex align="center" gap={12}>
              <Tag color={error ? "error" : "success"}>{error ? "接口异常" : "接口联调模式"}</Tag>
              <Button icon={loading ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />} onClick={refreshAll} loading={loading}>
                刷新
              </Button>
            </Flex>
          </Layout.Header>

          <Layout.Content className="content">
          {toast && <div className="toast">{toast}</div>}
          {error && <AntAlert type="error" showIcon message={error} className="page-alert" />}
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
          </Layout.Content>
        </Layout>
      </Layout>
    </ConfigProvider>
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
        <Button type="primary" icon={<Plus size={16} />} onClick={() => setPage("publish")}>
          发布任务
        </Button>
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
  const [title, setTitle] = useState("客户需求协同处理")
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
      const response = await createTaskRequest(title.trim(), content)
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
            title: task.title || task.draft?.title || taskTitle(task),
            description: draftTaskListText(task),
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

  function updateDraftTask(taskId: string, patch: Partial<{ title: string; description: string }>) {
    setDraftTasks((current) =>
      current.map((task) => {
        if (task.id !== taskId) return task
        return {
          ...task,
          ...(patch.title !== undefined ? { title: patch.title } : {}),
          draft: {
            ...(task.draft || { title: taskTitle(task), description: taskDescription(task) }),
            ...patch,
          },
        }
      }),
    )
  }

  async function closeIntentModal() {
    if (submitting || confirming) return
    const taskIds = draftTasks.map((task) => task.id)
    if (taskIds.length > 0) {
      setConfirming(true)
      setMessage("")
      setIntentError("")
      try {
        await Promise.all(taskIds.map((taskId) => cancelTask(taskId)))
        onCancelled(taskIds)
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : "取消任务失败"
        setIntentError(errorMessage)
        setMessage(`取消失败：${errorMessage}`)
        setConfirming(false)
        return
      }
      setConfirming(false)
    }
    setDraftTasks([])
    setIntentError("")
    setIntentModalOpen(false)
  }

  return (
    <div className="page active">
      <PageHeader title="任务发布页" description="填写任务名称和任务诉求，系统会识别并整理待确认的任务清单。" />
      <Card className="form-panel">
      <form onSubmit={submit}>
        <label className="field">
          <span>任务名称（50字以内）</span>
          <Input
            showCount
            maxLength={50}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="请输入任务名称"
          />
        </label>
        <label className="field">
          <span>任务诉求</span>
          <Input.TextArea rows={7} value={content} onChange={(event) => setContent(event.target.value)} />
        </label>
        <div className="form-actions">
          <Button type="primary" htmlType="submit" icon={<Send size={16} />} loading={submitting} disabled={!title.trim() || title.trim().length > 50 || !content.trim()}>
            提交请求
          </Button>
          {message && <Typography.Text type="danger">{message}</Typography.Text>}
        </div>
      </form>
      </Card>
      <Modal
        title="意图识别任务清单"
        open={intentModalOpen}
        width={860}
        onCancel={() => void closeIntentModal()}
        footer={submitting || intentError ? null : [
          <Button key="cancel" onClick={() => void closeIntentModal()} disabled={confirming}>取消</Button>,
          <Button key="confirm" type="primary" onClick={confirmDrafts} loading={confirming} disabled={draftTasks.length === 0}>确认并执行</Button>,
        ]}
        maskClosable={false}
        closable={!submitting && !confirming}
      >
        <Typography.Paragraph type="secondary">
          {submitting ? "正在拆分整理任务清单，请稍后" : "请确认识别出的任务名称和描述，确认后系统会异步执行。"}
        </Typography.Paragraph>
            {submitting ? (
              <div className="intent-loading">
                <Spin size="large" />
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
                  {draftTasks.map((task) => (
                    <Card className="intent-task-card" key={task.id} size="small">
                      <div className="intent-task-index">
                        <Tag color="blue">{taskLabel()}</Tag>
                        <Typography.Text type="secondary">可编辑任务名称和描述</Typography.Text>
                      </div>
                      <label className="field">
                        <span>任务名称</span>
                        <Input value={draftTitleValue(task)} onChange={(event) => updateDraftTask(task.id, { title: event.target.value })} />
                      </label>
                      <label className="field">
                        <span>任务描述</span>
                        <Input.TextArea rows={5} value={draftDescriptionValue(task)} onChange={(event) => updateDraftTask(task.id, { description: event.target.value })} />
                      </label>
                    </Card>
                  ))}
                </div>
              </>
            )}
      </Modal>
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
            <List
              dataSource={humanSubtasks}
              renderItem={(subtask) => (
                <List.Item
                  className={subtask.id === active.id ? "list-item active" : "list-item"}
                  onClick={() => setActiveId(subtask.id)}
                >
                  <List.Item.Meta
                    title={subtask.title || subtask.description || subtask.id}
                    description={subtask.current_node || subtask.assignee_type || "human"}
                  />
                  <Tag color={statusColor(subtask.status)}>{statusText(subtask.status)}</Tag>
                </List.Item>
              )}
            />
          </Panel>
          <Panel title="人工处理">
            <Descriptions bordered size="small" column={1} className="human-subtask-detail">
              <Descriptions.Item label="子任务名称">{active.title || active.id}</Descriptions.Item>
              <Descriptions.Item label="子任务描述">{active.description || "-"}</Descriptions.Item>
              <Descriptions.Item label="任务 ID">{active.task_id || "-"}</Descriptions.Item>
              <Descriptions.Item label="处理节点">{active.current_node || "human"}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={statusColor(active.status)}>{statusText(active.status)}</Tag></Descriptions.Item>
            </Descriptions>
            <label className="field">
              <span>处理意见</span>
              <Input.TextArea rows={5} value={opinion} onChange={(event) => setOpinion(event.target.value)} placeholder="填写人工判断、补充信息或驳回原因" />
            </label>
            <div className="form-actions">
              <Button type="primary" icon={<CheckCircle2 size={16} />} onClick={() => void submit("approved")} loading={submitting}>
                确认通过
              </Button>
              <Button danger icon={<XCircle size={16} />} onClick={() => void submit("rejected")} disabled={submitting}>
                驳回
              </Button>
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
  onOpenHumanWorkbench,
  onClose,
}: {
  task: Task
  loading: boolean
  error: string
  onOpenHumanWorkbench: () => void
  onClose: () => void
}) {
  return (
    <Modal
      title={
        <div className="task-detail-title">
          <Tooltip title={taskTitle(task)}>
            <Typography.Text strong ellipsis>{taskTitle(task)}</Typography.Text>
          </Tooltip>
        </div>
      }
      open
      onCancel={onClose}
      footer={null}
      width="min(1240px, calc(100vw - 56px))"
      style={{ top: 16, height: "calc(100vh - 32px)", maxHeight: 920, minHeight: 760 }}
      styles={{
        body: {
          flex: "1 1 auto",
          minHeight: 0,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        },
      }}
      className="task-detail-modal"
    >
        <div className="task-detail-body">
          <div className="task-detail-summary">
            {loading && <AntAlert type="info" showIcon message="正在加载最新详情" />}
            {error && <AntAlert type="error" showIcon message={error} />}
            <section className="detail-text-block">
              <h4>原始诉求</h4>
              <div>{task.content || task.description || "-"}</div>
            </section>
            <section className="detail-text-block">
              <h4>任务清单</h4>
              <div>{draftTaskListText(task)}</div>
            </section>
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
    </Modal>
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
        <Card className="form-panel" title="极简创建 Agent" size="small">
        <form onSubmit={submit}>
          <label className="field">
            <span>Agent 名称</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="field">
            <span>能力诉求</span>
            <Input.TextArea rows={6} value={ability} onChange={(event) => setAbility(event.target.value)} />
          </label>
          <Button type="primary" htmlType="submit" icon={<Bot size={16} />} loading={submitting} disabled={!ability.trim()}>
            极简创建 Agent
          </Button>
          {result && (
            <AntAlert
              className="agent-result"
              type={result.status === "created" ? "success" : "warning"}
              showIcon
              message={result.status}
              description={<>{result.message}{result.guidance?.map((item) => <p key={item}>{item}</p>)}</>}
            />
          )}
        </form>
        </Card>
        <Panel title="已注册 Agent">
          <List
            dataSource={agents}
            renderItem={(agent) => (
              <List.Item>
                <List.Item.Meta
                  title={agent.name}
                  description={
                    <div>
                      <Typography.Paragraph ellipsis={{ rows: 2 }}>{agent.description}</Typography.Paragraph>
                      <Flex wrap gap={6}>
                        {(agent.capabilities || []).slice(0, 4).map((capability) => <Tag key={capability}>{capability}</Tag>)}
                        <Tag color="blue">{(agent.tools || []).map((tool) => tool.type).join("、") || "无工具"}</Tag>
                      </Flex>
                    </div>
                  }
                />
              </List.Item>
            )}
          />
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
        <Typography.Title level={3} className="page-title">{title}</Typography.Title>
        <Typography.Paragraph className="page-description">{description}</Typography.Paragraph>
      </div>
      <div className="page-header-actions">{children}</div>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="panel" title={title} size="small">
      {children}
    </Card>
  )
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <Card className={`metric-card ${tone}`} size="small">
      <Statistic title={label} value={value} valueStyle={{ color: toneColor(tone), fontSize: 26 }} />
    </Card>
  )
}

function TaskTable({ tasks, compact, onSelect, selectedTaskId }: { tasks: Task[]; compact?: boolean; onSelect?: (id: string) => void; selectedTaskId?: string }) {
  const [page, setPage] = useState(1)
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
  const columns: ColumnsType<Task> = [
    {
      title: "任务名称",
      dataIndex: "title",
      ellipsis: true,
      render: (_, task) => (
        <TableCellTooltip text={taskTitle(task)} />
      ),
    },
    ...(!compact ? [{
      title: "任务描述",
      dataIndex: "description",
      ellipsis: true,
      render: (_: unknown, task: Task) => (
        <TableCellTooltip text={taskDescription(task)} />
      ),
    } as ColumnsType<Task>[number]] : []),
    ...(!compact ? [{
      title: "节点",
      dataIndex: "current_node",
      width: 160,
      render: (value: string) => value || "-",
    } as ColumnsType<Task>[number]] : []),
    {
      title: "状态",
      width: 110,
      render: (_, task) => <Tag color={statusColor(taskStatus(task))}>{statusText(taskStatus(task))}</Tag>,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 130,
      render: (value: string) => formatDate(value),
    },
    ...(onSelect ? [{
      title: "操作",
      width: 90,
      render: (_: unknown, task: Task) => <Button size="small" onClick={() => onSelect(task.id)}>详情</Button>,
    } as ColumnsType<Task>[number]] : []),
  ]
  return (
    <>
      <Table
        rowKey="id"
        size="middle"
        columns={columns}
        dataSource={visibleTasks}
        pagination={false}
        rowClassName={(task) => selectedTaskId === task.id ? "selected" : ""}
      />
      {!compact && (
        <div className="pagination">
          <span className="muted">第 {Math.min(page, totalPages)} / {totalPages} 页，共展示前 {Math.min(sortedTasks.length, 200)} 条，每页 20 条</span>
          <Pagination current={Math.min(page, totalPages)} total={Math.min(sortedTasks.length, 200)} pageSize={20} showSizeChanger={false} onChange={setPage} />
        </div>
      )}
    </>
  )
}

function TableCellTooltip({ text }: { text: string }) {
  return (
    <Tooltip
      title={text}
      placement="topLeft"
      getPopupContainer={() => document.body}
      mouseEnterDelay={0.2}
      zIndex={3000}
    >
      <span className="table-ellipsis">{text}</span>
    </Tooltip>
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
  return <AntEmpty image={AntEmpty.PRESENTED_IMAGE_SIMPLE} description={text} />
}
