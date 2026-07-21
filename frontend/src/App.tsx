import {
  Alert as AntAlert,
  Button,
  Card,
  Checkbox,
  Collapse,
  ConfigProvider,
  Descriptions,
  Empty as AntEmpty,
  Flex,
  Input,
  Layout,
  Menu,
  Modal,
  Pagination,
  Popconfirm,
  Segmented,
  Select,
  Space,
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
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  Paperclip,
  Trash2,
  Users,
  Edit3,
  GitBranch,
  Sparkles,
  UserCheck,
  XCircle,
} from "lucide-react"
import {
  Background,
  Controls,
  Handle,
  NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Agent,
  SubTask,
  Task,
  TaskAttachment,
  TaskRerunPreflightResponse,
  TaskRound,
  User,
  UserCreatePayload,
  UserOption,
  WorkflowDefinition,
  WorkflowTemplate,
  createTaskRerun,
  createAgent,
  createHumanNodeForUser,
  createSimpleAgent,
  createTaskRequest,
  createUser,
  deleteUser,
  getCurrentUser,
  getTask,
  listAssignableUsers,
  listAgents,
  listHumanSubtasks,
  listTasks,
  listUsers,
  listWorkflows,
  preflightTaskRerun,
  setCurrentUserId,
  submitHumanSubtaskResult,
  submitTaskResult,
  updateUser,
  uploadTaskAttachment,
} from "./api/taskhub"
import { humanReviewDocumentSourceLabel, humanReviewDocumentText } from "./humanReview"
import { PageId, canNavigateToPage, refreshTargetsForPage } from "./pageRefresh"
import { initialPublishForm, validatePublishForm, validateWorkflowBuilderOpen } from "./publishForm"
import {
  isManualWorkflowTask,
  isTaskRerunnable,
  isTerminalTask,
  taskNodeText,
  taskStatus,
  taskStatusColor,
  taskStatusText,
  taskTypeText,
} from "./taskType"
import { isTaskAwaitingConfirmation } from "./taskConfirmation"
import { TaskConfirmationModal } from "./TaskConfirmationModal"
import {
  PendingTaskRerunRequest,
  canSubmitTaskRerun,
  clearPendingTaskRerun,
  ensurePendingTaskRerun,
  loadPendingTaskRerun,
  shouldBlockTaskRerunFormForPreflight,
} from "./taskRerunState"
import {
  buildTaskInterventionResultPayload,
  compactContextText,
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
  workflowNodeStateColor as detailWorkflowNodeStateColor,
} from "./taskDetailView"
import { TOAST_DISMISS_MS, ToastMessage, createToastMessage, shouldDismissToast } from "./toastState"
import { WorkflowBuilderPage } from "./WorkflowBuilderPage"
import { WorkflowReactFlowNode } from "./workflowReactFlow"

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
      { id: "agents", text: "流程节点管理", icon: Bot, adminOnly: true },
      { id: "users", text: "用户管理", icon: Users, adminOnly: true },
    ],
  },
] as const

function toneColor(tone: string) {
  return { info: "#2563eb", success: "#16a34a", warning: "#d97706", danger: "#dc2626" }[tone] || "#2563eb"
}

function taskTitle(task: Task) {
  return task.title || task.draft?.title || task.content || task.id
}

function taskDescription(task: Task) {
  return task.description || task.draft?.description || task.content || "-"
}

function taskAttachments(task: Task): TaskAttachment[] {
  const attachments = task.request_metadata?.attachments
  return Array.isArray(attachments) ? attachments : []
}

function formatFileSize(size?: number) {
  const value = size || 0
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function preferredAssignee(users: UserOption[], selectedUserId: string) {
  return users.find((user) => user.id === selectedUserId)
    || users.find((user) => user.id === "root")
    || users.find((user) => user.role === "admin")
    || users[0]
}

function assigneeConfirmPayload(user?: UserOption) {
  if (!user) return {}
  return {
    default_assignee_user_id: user.id,
    default_assignee_user_name: user.name,
    default_assignee_role: user.role,
  }
}

function taskResultText(task: Task) {
  return String(task.final_output || task.context?.summary || "").trim()
}

function displayValue(value: unknown) {
  if (value === undefined || value === null || value === "") return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function taskTypeColor(task: Task) {
  return isManualWorkflowTask(task) ? "purple" : "blue"
}

function workflowNodeKindText(type?: string) {
  return { start: "开始", end: "完成", agent: "Agent", human: "人工", condition: "条件" }[type || ""] || type || "节点"
}

function workflowNodeStateText(state: string) {
  return { pending: "未开始", running: "执行中", succeeded: "已完成", failed: "失败" }[state] || state
}

function workflowNodeStateColor(state: string) {
  return { pending: "default", running: "processing", succeeded: "success", failed: "error" }[state] || "default"
}

function workflowSubtaskForNode(task: Task, nodeId: string) {
  const prefix = `${task.id}_`
  return (task.context?.rounds || [])
    .flatMap((round) => round.subtasks || [])
    .find((subtask) => {
      const subtaskNodeId = subtask.id.startsWith(prefix) ? subtask.id.slice(prefix.length) : subtask.id
      return subtaskNodeId === nodeId
    })
}

function workflowNodeState(task: Task, node: WorkflowDefinition["nodes"][number]) {
  if (node.type === "start") return "succeeded"
  if (node.type === "end") return taskStatus(task) === "succeeded" ? "succeeded" : "pending"
  return workflowSubtaskForNode(task, node.id)?.status || "pending"
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
  const [users, setUsers] = useState<User[]>([])
  const [assignableUsers, setAssignableUsers] = useState<UserOption[]>([])
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [humanSubtasks, setHumanSubtasks] = useState<SubTask[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string>("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [toast, setToastState] = useState<ToastMessage | null>(null)
  const nextToastId = useRef(0)

  const events = useMemo(
    () =>
      tasks
        .flatMap((task) => (task.events || []).map((event) => ({ ...event, task_title: taskTitle(task), task_id: task.id })))
        .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at))),
    [tasks],
  )
  const isAdmin = currentUser?.role === "admin"
  const setToast = useCallback((value: string) => {
    nextToastId.current += 1
    setToastState(createToastMessage(value, nextToastId.current))
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => {
      setToastState((current) => shouldDismissToast(current, toast.id) ? null : current)
    }, TOAST_DISMISS_MS)
    return () => window.clearTimeout(timer)
  }, [toast])

  async function refreshAll() {
    setLoading(true)
    setError("")
    try {
      const nextCurrentUser = await getCurrentUser()
      const [nextTasks, nextAgents, nextHumanSubtasks, nextAssignableUsers] = await Promise.all([
        listTasks(),
        listAgents(),
        listHumanSubtasks(),
        listAssignableUsers(),
      ])
      const nextUsers = nextCurrentUser.role === "admin" ? await listUsers() : []
      setCurrentUser(nextCurrentUser)
      setTasks(nextTasks || [])
      setAgents(nextAgents || [])
      setAssignableUsers(nextAssignableUsers || [])
      setUsers(nextUsers || [])
      setHumanSubtasks(nextHumanSubtasks || [])
      if (!selectedTaskId && nextTasks?.[0]) setSelectedTaskId(nextTasks[0].id)
      if (nextCurrentUser.role !== "admin" && ["agents", "users"].includes(page)) {
        setPage("overview")
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "刷新失败")
    } finally {
      setLoading(false)
    }
  }

  async function refreshPageData(nextPage: PageId) {
    const targets = refreshTargetsForPage(nextPage, isAdmin)
    if (!targets.length) return

    setLoading(true)
    setError("")
    try {
      const targetSet = new Set(targets)
      const [nextTasks, nextHumanSubtasks, nextAgents, nextAssignableUsers, nextUsers] = await Promise.all([
        targetSet.has("tasks") ? listTasks() : Promise.resolve(null),
        targetSet.has("humanSubtasks") ? listHumanSubtasks() : Promise.resolve(null),
        targetSet.has("agents") ? listAgents() : Promise.resolve(null),
        targetSet.has("assignableUsers") ? listAssignableUsers() : Promise.resolve(null),
        targetSet.has("users") ? listUsers() : Promise.resolve(null),
      ])

      if (nextTasks) {
        setTasks(nextTasks || [])
        if (!selectedTaskId && nextTasks?.[0]) setSelectedTaskId(nextTasks[0].id)
      }
      if (nextHumanSubtasks) setHumanSubtasks(nextHumanSubtasks || [])
      if (nextAgents) setAgents(nextAgents || [])
      if (nextAssignableUsers) setAssignableUsers(nextAssignableUsers || [])
      if (nextUsers) setUsers(nextUsers || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : "页面数据刷新失败")
    } finally {
      setLoading(false)
    }
  }

  async function navigateTo(nextPage: PageId) {
    if (!canNavigateToPage(nextPage, isAdmin)) return
    setPage(nextPage)
    await refreshPageData(nextPage)
  }

  useEffect(() => {
    void refreshAll()
  }, [])

  async function switchCurrentUser(userId: string) {
    setCurrentUserId(userId)
    setSelectedTaskId("")
    await refreshAll()
  }

  const userSwitchOptions = useMemo(() => {
    const optionMap = new Map<string, { value: string; label: string }>()
    ;[...users, ...assignableUsers].forEach((user) => {
      optionMap.set(user.id, { value: user.id, label: user.name })
    })
    return Array.from(optionMap.values())
  }, [assignableUsers, users])

  const menuItems = navGroups.flatMap((group) => [
    {
      type: "group" as const,
      label: group.label,
      children: group.items.filter((item) => isAdmin || !("adminOnly" in item && item.adminOnly)).map((item) => {
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
              placeholder="搜索任务、流程节点、执行节点"
              readOnly
            />
            <Flex align="center" gap={12}>
              <Select
                className="user-switcher"
                value={currentUser?.id || "root"}
                options={userSwitchOptions}
                onChange={(value) => void switchCurrentUser(value)}
                placeholder="当前用户"
              />
              <Tag color={isAdmin ? "gold" : "blue"}>{isAdmin ? "管理员" : "普通用户"}</Tag>
              <Tag color={error ? "error" : "success"}>{error ? "接口异常" : "接口联调模式"}</Tag>
              <Button icon={loading ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />} onClick={refreshAll} loading={loading}>
                刷新
              </Button>
            </Flex>
          </Layout.Header>

          <Layout.Content className="content">
          {toast && <div className="toast" key={toast.id}>{toast.text}</div>}
          {error && <AntAlert type="error" showIcon title={error} className="page-alert" />}
          {page === "overview" && <Overview tasks={tasks} agents={agents} humanSubtasks={humanSubtasks} events={events} setPage={(nextPage) => void navigateTo(nextPage)} />}
          {page === "publish" && <PublishPage agents={agents} users={assignableUsers} setToast={setToast} onCreated={(created) => {
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
          {page === "tasks" && <TasksPage tasks={tasks} setSelectedTaskId={setSelectedTaskId} onTaskUpdated={(updated) => {
            setTasks((current) => mergeTasks(current, [updated]))
            setSelectedTaskId(updated.id)
          }} onOpenHumanWorkbench={async () => {
            await refreshAll()
            setPage("confirmation")
          }} />}
          {page === "agents" && isAdmin && <AgentsPage agents={agents} users={assignableUsers} setAgents={setAgents} setToast={setToast} />}
          {page === "users" && isAdmin && <UsersPage users={users} setUsers={setUsers} setToast={setToast} />}
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
        <Metric label="流程节点" value={agents.length} tone="info" />
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
  agents,
  users,
  setToast,
  onCreated,
  onConfirmed,
  onCancelled,
}: {
  agents: Agent[]
  users: UserOption[]
  setToast: (value: string) => void
  onCreated: (tasks: Task[]) => void
  onConfirmed: (tasks: Task[]) => void
  onCancelled: (taskIds: string[]) => void
}) {
  const initialForm = initialPublishForm()
  const [title, setTitle] = useState(initialForm.title)
  const [content, setContent] = useState(initialForm.content)
  const [draftTasks, setDraftTasks] = useState<Task[]>([])
  const [intentModalOpen, setIntentModalOpen] = useState(false)
  const [intentError, setIntentError] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState("")
  const [workflows, setWorkflows] = useState<WorkflowTemplate[]>([])
  const [workflowId, setWorkflowId] = useState(initialForm.workflowId)
  const [workflowLoading, setWorkflowLoading] = useState(false)
  const [workflowError, setWorkflowError] = useState("")
  const [workflowModalOpen, setWorkflowModalOpen] = useState(false)
  const [attachments, setAttachments] = useState<TaskAttachment[]>([])
  const [attachmentUploading, setAttachmentUploading] = useState(false)
  const [attachmentError, setAttachmentError] = useState("")
  const [defaultAssigneeUserId, setDefaultAssigneeUserId] = useState("")
  const attachmentInputRef = useRef<HTMLInputElement | null>(null)
  const attachmentIds = attachments.map((attachment) => attachment.id)
  const selectedAssignee = preferredAssignee(users, defaultAssigneeUserId)

  useEffect(() => {
    let cancelled = false
    setWorkflowLoading(true)
    setWorkflowError("")
    listWorkflows()
      .then((items) => {
        if (!cancelled) setWorkflows(items || [])
      })
      .catch((err) => {
        if (!cancelled) setWorkflowError(err instanceof Error ? err.message : "流程模板加载失败")
      })
      .finally(() => {
        if (!cancelled) setWorkflowLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!users.length) return
    if (!users.some((user) => user.id === defaultAssigneeUserId)) {
      setDefaultAssigneeUserId(preferredAssignee(users, "")?.id || "")
    }
  }, [users, defaultAssigneeUserId])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const validationMessage = validatePublishForm(title, content)
    if (validationMessage) {
      setMessage(validationMessage)
      return
    }
    setSubmitting(true)
    setMessage("")
    setIntentError("")
    setDraftTasks([])
    setIntentModalOpen(true)
    try {
      const response = await createTaskRequest(title.trim(), content, workflowId, "business_system", attachmentIds)
      const created = response.tasks || []
      onCreated(created)
      openTaskConfirmation(created)
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "提交失败"
      setIntentError(errorMessage)
      setMessage(errorMessage)
    } finally {
      setSubmitting(false)
    }
  }

  function openWorkflowBuilder() {
    setMessage("")
    const validationMessage = validateWorkflowBuilderOpen(title, content)
    if (validationMessage) {
      setMessage(validationMessage)
      return
    }
    setWorkflowModalOpen(true)
  }

  function rememberWorkflow(saved: WorkflowTemplate) {
    setWorkflows((current) => {
      const savedIds = new Set([saved.id])
      return [saved, ...current.filter((workflow) => !savedIds.has(workflow.id))]
    })
  }

  function openTaskConfirmation(created: Task[]) {
    setDraftTasks(created)
    setIntentError("")
    setIntentModalOpen(true)
  }

  function closeTaskConfirmation() {
    setDraftTasks([])
    setIntentError("")
    setIntentModalOpen(false)
  }

  async function handleAttachmentFiles(fileList: FileList | null) {
    if (!fileList?.length) return
    setAttachmentUploading(true)
    setAttachmentError("")
    const allowedExtensions = new Set([".docx", ".xlsx", ".txt", ".md", ".log"])
    try {
      for (const file of Array.from(fileList)) {
        const extension = `.${file.name.split(".").pop()?.toLowerCase() || ""}`
        if (!allowedExtensions.has(extension)) {
          setAttachmentError("仅支持 .docx、.xlsx、.txt、.md、.log 文本附件")
          continue
        }
        if (file.size > 10 * 1024 * 1024) {
          setAttachmentError("单个附件不能超过 10MB")
          continue
        }
        const uploaded = await uploadTaskAttachment(file)
        setAttachments((current) => {
          if (current.some((attachment) => attachment.id === uploaded.id)) return current
          return [...current, uploaded]
        })
        setToast(`附件已解析：${uploaded.filename}`)
      }
    } catch (err) {
      setAttachmentError(err instanceof Error ? err.message : "附件上传失败")
    } finally {
      setAttachmentUploading(false)
      if (attachmentInputRef.current) attachmentInputRef.current.value = ""
    }
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
          <div className="field attachment-field">
            <span>文本附件（可选）</span>
            <input
              ref={attachmentInputRef}
              className="hidden-file-input"
              type="file"
              multiple
              accept=".docx,.xlsx,.txt,.md,.log"
              onChange={(event) => void handleAttachmentFiles(event.target.files)}
            />
            <div className="attachment-upload-panel">
              <Button icon={<Paperclip size={16} />} loading={attachmentUploading} onClick={() => attachmentInputRef.current?.click()}>
                上传文本附件
              </Button>
              <Typography.Text type="secondary">支持 .docx、.xlsx、.txt、.md、.log，仅解析纯文本，单个文件不超过 10MB。</Typography.Text>
            </div>
            {attachmentError && <Typography.Text type="danger">{attachmentError}</Typography.Text>}
            {!!attachments.length && (
              <div className="attachment-list">
                {attachments.map((attachment) => (
                  <div className="attachment-item" key={attachment.id}>
                    <div>
                      <Typography.Text strong>{attachment.filename}</Typography.Text>
                      <span>{formatFileSize(attachment.size_bytes)} / {attachment.text_length || 0} 字符{attachment.truncated ? " / 已截断" : ""}</span>
                    </div>
                    <Button
                      size="small"
                      icon={<Trash2 size={14} />}
                      onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))}
                    >
                      移除
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <label className="field">
            <span>流程模板（可选）</span>
            <Select
              allowClear
              showSearch
              loading={workflowLoading}
              value={workflowId || undefined}
              placeholder="不选择则按无模板协同流程执行"
              optionFilterProp="label"
              options={workflows.map((workflow) => ({
                value: workflow.id,
                label: workflow.name,
              }))}
              onChange={(value) => setWorkflowId(value || "")}
            />
          </label>
          <div className="form-actions">
            <Button type="primary" htmlType="submit" icon={<Send size={16} />} loading={submitting}>
              提交请求
            </Button>
            {message && <Typography.Text type="danger">{message}</Typography.Text>}
            {workflowError && <Typography.Text type="secondary">流程模板加载失败，仍可按无模板流程提交</Typography.Text>}
          </div>
        </form>
        <Card size="small" className="workflow-publish-card">
          <Flex align="center" justify="space-between" gap={16} wrap>
            <div>
              <Typography.Text strong>流程节点编排</Typography.Text>
              <div className="muted">打开大画布选择 Agent、拖动连线、配置人工节点和执行交代，保存后可在流程模板下拉框中选择。</div>
            </div>
            <Button icon={<Bot size={16} />} onClick={openWorkflowBuilder}>
              打开流程编排
            </Button>
          </Flex>
        </Card>
      </Card>
      <TaskConfirmationModal
        title="意图识别任务清单"
        open={intentModalOpen}
        tasks={draftTasks}
        preparing={submitting}
        preparationError={intentError}
        confirmOptions={{
          execution_mode: "async",
          ...assigneeConfirmPayload(selectedAssignee),
        }}
        beforeTasks={(
          <div className="intent-assignee-panel">
            <div>
              <Typography.Text strong>默认人工处理人</Typography.Text>
              <span>后续如果拆出人工节点，会优先分配给该人员；不选则由管理员处理。</span>
            </div>
            <Select
              value={selectedAssignee?.id}
              placeholder="选择默认人工处理人"
              optionFilterProp="label"
              options={users.map((user) => ({
                value: user.id,
                label: `${user.name}${user.role === "admin" ? "（管理员）" : ""}`,
              }))}
              onChange={setDefaultAssigneeUserId}
            />
          </div>
        )}
        onTaskUpdated={(confirmed) => {
          onConfirmed([confirmed])
          setMessage("任务已确认，系统正在异步执行")
        }}
        onTasksCancelled={(taskIds) => {
          onCancelled(taskIds)
          setMessage("任务清单已取消")
        }}
        onClose={closeTaskConfirmation}
      />
      <Modal
        title="流程节点编排"
        open={workflowModalOpen}
        width="min(1680px, 96vw)"
        footer={null}
        destroyOnHidden
        mask={{ closable: false }}
        className="agent-workflow-modal"
        onCancel={() => {
          setWorkflowModalOpen(false)
        }}
      >
        <WorkflowBuilderPage
          modal
          agents={agents}
          users={users}
          workflows={workflows}
          onWorkflowSaved={rememberWorkflow}
          setToast={setToast}
        />
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
  const upstreamOutputs = active?.upstream_outputs || []
  const taskArtifacts = (active?.task_artifacts || []).map(displayValue).filter(Boolean)
  const reviewDocument = humanReviewDocumentText(active)
  const reviewSourceLabel = humanReviewDocumentSourceLabel(active)

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
        <div className="human-workbench">
          <aside className="human-queue-panel">
            <div className="human-queue-head">
              <div>
                <span>待审核队列</span>
                <strong>{humanSubtasks.length}</strong>
              </div>
              <Tag color="processing">人工节点</Tag>
            </div>
            <div className="human-queue-list">
              {humanSubtasks.map((subtask) => (
                <button
                  type="button"
                  className={subtask.id === active.id ? "human-queue-card active" : "human-queue-card"}
                  key={subtask.id}
                  onClick={() => setActiveId(subtask.id)}
                >
                  <span className="human-queue-card-head">
                    <span>{subtask.title || subtask.description || subtask.id}</span>
                    <Tag color={taskStatusColor(subtask.status)}>{taskStatusText(subtask.status)}</Tag>
                  </span>
                  <span className="human-queue-task">{subtask.task_title || "未命名任务"}</span>
                  <span className="human-queue-meta">
                    <UserCheck size={13} />
                    {subtask.assignee_user_name || "未指定人员"} · {humanReviewDocumentSourceLabel(subtask)}
                  </span>
                </button>
              ))}
            </div>
          </aside>
          <section className="human-review-panel">
            <div className="human-review-hero">
              <div>
                <span className="human-review-eyebrow"><ClipboardCheck size={15} /> 当前审核</span>
                <h3>{active.task_title || active.title || "人工确认"}</h3>
                <p>{active.task_content || active.task_description || active.description || "暂无原始诉求"}</p>
              </div>
              <div className="human-review-status">
                <Tag color={taskStatusColor(active.status)}>{taskStatusText(active.status)}</Tag>
                <Tag color="blue">{active.assignee_user_name || "管理员"}</Tag>
              </div>
            </div>
            <section className="human-review-document">
              <header>
                <div>
                  <FileText size={16} />
                  <strong>待审核文档</strong>
                </div>
                <Tag color={upstreamOutputs.length ? "cyan" : reviewDocument ? "blue" : "default"}>{reviewSourceLabel}</Tag>
              </header>
              <pre className={reviewDocument ? "" : "empty"}>{reviewDocument || "暂无待审核文档"}</pre>
            </section>
            <div className="human-review-support">
              <section className="human-subtask-card">
                <div>
                  <ListChecks size={16} />
                  <strong>审核节点</strong>
                </div>
                <Descriptions bordered size="small" column={1} className="human-subtask-detail">
                  <Descriptions.Item label="子任务名称">{active.title || active.id}</Descriptions.Item>
                  <Descriptions.Item label="子任务描述">{active.description || "-"}</Descriptions.Item>
                  <Descriptions.Item label="任务 ID">{active.task_id || "-"}</Descriptions.Item>
                  <Descriptions.Item label="处理节点">{active.current_node || "human"}</Descriptions.Item>
                </Descriptions>
              </section>
              <section className="human-context-panel">
                <header>
                  <div>
                    <FileText size={15} />
                    <strong>辅助上下文</strong>
                  </div>
                  <Tag color="geekblue">{upstreamOutputs.length} 个上游产出</Tag>
                </header>
                <p>{active.task_context_summary || "暂无上下文汇总"}</p>
                {!!taskArtifacts.length && (
                  <div className="human-context-artifacts">
                    {taskArtifacts.map((artifact, index) => (
                      <Tag key={`${artifact}-${index}`} color="geekblue">{artifact}</Tag>
                    ))}
                  </div>
                )}
              </section>
            </div>
            <label className="field human-review-opinion">
              <span>处理意见</span>
              <Input.TextArea rows={5} value={opinion} onChange={(event) => setOpinion(event.target.value)} placeholder="填写人工判断、补充信息或驳回原因" />
            </label>
            <div className="human-review-actions">
              <Button type="primary" icon={<CheckCircle2 size={16} />} onClick={() => void submit("approved")} loading={submitting}>
                确认通过
              </Button>
              <Button danger icon={<XCircle size={16} />} onClick={() => void submit("rejected")} disabled={submitting}>
                驳回
              </Button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

function TasksPage({
  tasks,
  setSelectedTaskId,
  onTaskUpdated,
  onOpenHumanWorkbench,
}: {
  tasks: Task[]
  setSelectedTaskId: (id: string) => void
  onTaskUpdated: (task: Task) => void
  onOpenHumanWorkbench: () => Promise<void>
}) {
  const [detailTask, setDetailTask] = useState<Task | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [confirmationTask, setConfirmationTask] = useState<Task | null>(null)

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
        <TaskTable
          tasks={tasks}
          onSelect={openDetail}
          onContinueConfirmation={setConfirmationTask}
          onTaskUpdated={onTaskUpdated}
          selectedTaskId={detailTask?.id}
        />
      </Panel>
      {detailTask && (
        <TaskDetailModal
          task={detailTask}
          loading={detailLoading}
          error={detailError}
          onTaskUpdated={(updated) => {
            setDetailTask(updated)
            onTaskUpdated(updated)
          }}
          onContinueConfirmation={() => setConfirmationTask(detailTask)}
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
      <TaskConfirmationModal
        title="继续确认任务"
        open={Boolean(confirmationTask)}
        tasks={confirmationTask ? [confirmationTask] : []}
        cancelOnClose={false}
        onTaskUpdated={(updated) => {
          setDetailTask(updated)
          onTaskUpdated(updated)
        }}
        onTasksCancelled={async ([taskId]) => {
          try {
            const updated = await getTask(taskId)
            setDetailTask(updated)
            onTaskUpdated(updated)
          } catch (err) {
            setDetailError(err instanceof Error ? err.message : "任务已取消，请刷新任务列表")
          }
        }}
        onClose={() => setConfirmationTask(null)}
      />
    </div>
  )
}

function TaskDetailModal({
  task,
  loading,
  error,
  onTaskUpdated,
  onContinueConfirmation,
  onOpenHumanWorkbench,
  onClose,
}: {
  task: Task
  loading: boolean
  error: string
  onTaskUpdated: (task: Task) => void
  onContinueConfirmation: () => void
  onOpenHumanWorkbench: () => void
  onClose: () => void
}) {
  const attachments = taskAttachments(task)
  const typeBadge = taskDetailTypeBadge(task)
  const summaryBlocks = taskDetailSummaryBlocks(task)
  const fourQuestions = taskFourQuestions(task)
  const workflowDefinition = workflowDefinitionForTask(task)

  return (
    <Modal
      title={
        <div className="task-detail-title">
          <Tooltip title={taskTitle(task)}>
            <Typography.Text strong ellipsis>{taskTitle(task)}</Typography.Text>
          </Tooltip>
          <Tag color={typeBadge.color}>{typeBadge.text}</Tag>
          {isTaskAwaitingConfirmation(task) && (
            <Button
              size="small"
              type="primary"
              aria-label="继续确认任务"
              icon={<ClipboardCheck size={15} />}
              onClick={onContinueConfirmation}
            >
              继续确认
            </Button>
          )}
          <TaskRerunControl key={task.id} task={task} onTaskUpdated={onTaskUpdated} />
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
          <TaskFourQuestionGrid questions={fourQuestions} />
          <div className="task-detail-summary">
            {loading && <AntAlert type="info" showIcon title="正在加载最新详情" />}
            {error && <AntAlert type="error" showIcon title={error} />}
            {summaryBlocks.map((block) => (
              <section className="detail-text-block" key={block.key}>
                <h4>{block.title}</h4>
                <div>{block.text}</div>
              </section>
            ))}
          </div>
          <TaskResultDetail task={task} />
          <ExecutionHistory key={task.id} task={task} />
          <TaskInterventionPanel task={task} onTaskUpdated={onTaskUpdated} />
          {!!attachments.length && <TaskAttachmentDetail attachments={attachments} />}
          {isManualWorkflowTask(task) ? (
            <section className="execution-section">
              <h4>手动编排流程</h4>
              {workflowDefinition ? (
                <ManualWorkflowDetail task={task} definition={workflowDefinition} />
              ) : (
                <EmptyState text="暂无手动编排流程" />
              )}
            </section>
          ) : (
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
          )}
          <TaskContextDetail task={task} />
        </div>
    </Modal>
  )
}

function TaskFourQuestionGrid({
  questions,
}: {
  questions: ReturnType<typeof taskFourQuestions>
}) {
  return (
    <section className="task-four-questions" aria-label="任务四个核心问题">
      {questions.map((question) => (
        <div className="task-four-question" key={question.key}>
          <span>{question.title}</span>
          <strong>{question.text}</strong>
        </div>
      ))}
    </section>
  )
}

function TaskRerunControl({
  task,
  onTaskUpdated,
}: {
  task: Task
  onTaskUpdated: (task: Task) => void
}) {
  const [open, setOpen] = useState(false)
  const [preflight, setPreflight] = useState<TaskRerunPreflightResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [reason, setReason] = useState("")
  const [executionMode, setExecutionMode] = useState<"sync" | "async">("async")
  const [confirmSideEffects, setConfirmSideEffects] = useState(false)
  const [pendingRequest, setPendingRequest] = useState<PendingTaskRerunRequest | null>(
    () => loadPendingTaskRerun(task.id),
  )
  const [error, setError] = useState("")
  const [outcome, setOutcome] = useState("")
  const sourceExecutionId = task.active_execution_id || ""
  const canRerun = isTaskRerunnable(task) && Boolean(sourceExecutionId)
  const canOpenRerun = canRerun || Boolean(pendingRequest)

  useEffect(() => {
    const restored = loadPendingTaskRerun(task.id)
    setPendingRequest(restored)
    if (restored) applyPendingRequest(restored)
  }, [task.id])

  function applyPendingRequest(request: PendingTaskRerunRequest) {
    setReason(request.payload.reason)
    setExecutionMode(request.payload.execution_mode || "sync")
    setConfirmSideEffects(Boolean(request.payload.confirm_side_effects))
  }

  async function loadPreflight(sourceId: string, preservePendingError = false) {
    setLoading(true)
    try {
      setPreflight(await preflightTaskRerun(task.id, {
        source_execution_id: sourceId,
      }))
      if (!preservePendingError) setError("")
    } catch (err) {
      setError(readableRerunError(err, "重跑预检失败"))
    } finally {
      setLoading(false)
    }
  }

  async function openRerun() {
    const restored = loadPendingTaskRerun(task.id) || pendingRequest
    if (!sourceExecutionId && !restored) return
    setOpen(true)
    setPreflight(null)
    setError("")
    setOutcome("")
    if (restored) {
      setPendingRequest(restored)
      applyPendingRequest(restored)
      await loadPreflight(restored.payload.source_execution_id, true)
      return
    }
    setReason("")
    setExecutionMode("async")
    setConfirmSideEffects(false)
    await loadPreflight(sourceExecutionId)
  }

  async function submitRerun() {
    if (submitting) return
    let request = pendingRequest || loadPendingTaskRerun(task.id)
    if (!request) {
      const trimmedReason = reason.trim()
      if (!trimmedReason) {
        setError("请填写重跑理由")
        return
      }
      if (!preflight?.allowed) {
        setError("当前任务未通过重跑预检")
        return
      }
      if (preflight.requires_side_effect_confirmation && !confirmSideEffects) {
        setError("请确认重跑可能重复触发外部副作用")
        return
      }
      try {
        request = ensurePendingTaskRerun(task.id, {
          source_execution_id: preflight.source_execution_id,
          reason: trimmedReason,
          execution_mode: executionMode,
          confirm_side_effects: confirmSideEffects,
        })
        setPendingRequest(request)
        applyPendingRequest(request)
      } catch (err) {
        setError(err instanceof Error ? err.message : "无法保存待确认的重跑请求")
        return
      }
    }
    setSubmitting(true)
    setError("")
    try {
      const response = await createTaskRerun(
        task.id,
        request.payload,
        request.idempotencyKey,
      )
      clearPendingTaskRerun(task.id)
      setPendingRequest(null)
      onTaskUpdated(response.task)
      setOutcome([
        response.replayed ? "请求命中幂等回放" : `已创建第 ${response.execution.attempt_no} 次执行`,
        response.execution_is_active ? "该执行当前有效" : "该执行已不是当前活动执行",
        response.scheduled ? "已进入后台调度" : "请求已处理",
      ].join("；"))
    } catch (err) {
      setError(readableRerunError(err, "重跑创建失败"))
    } finally {
      setSubmitting(false)
    }
  }

  async function discardPendingRequest() {
    clearPendingTaskRerun(task.id)
    setPendingRequest(null)
    setPreflight(null)
    setReason("")
    setExecutionMode("async")
    setConfirmSideEffects(false)
    setError("")
    setOutcome("")
    if (canRerun && sourceExecutionId) {
      await loadPreflight(sourceExecutionId)
    } else {
      setOpen(false)
    }
  }

  if (!canOpenRerun && !open) return null

  return (
    <>
      {canOpenRerun && (
        <Tooltip title={pendingRequest ? "确认上一次重跑请求的提交结果" : "基于当前执行创建一次重跑"}>
          <Button
            size="small"
            type="text"
            aria-label={pendingRequest ? "确认上一次重跑请求的提交结果" : "重跑任务"}
            icon={<RotateCcw size={15} />}
            onClick={() => void openRerun()}
          >
            {pendingRequest ? "待确认" : "重跑"}
          </Button>
        </Tooltip>
      )}
      <Modal
        title="重跑任务"
        open={open}
        width={680}
        onCancel={() => setOpen(false)}
        mask={{ closable: false }}
        footer={[
          <Button key="close" onClick={() => setOpen(false)} disabled={submitting}>关闭</Button>,
          <Button
            key="submit"
            type="primary"
            icon={<RotateCcw size={15} />}
            loading={submitting}
            disabled={
              Boolean(outcome)
              || (!pendingRequest && loading)
              || !canSubmitTaskRerun({
                pendingRequest,
                preflightAllowed: Boolean(preflight?.allowed),
                reason,
                requiresSideEffectConfirmation: Boolean(preflight?.requires_side_effect_confirmation),
                confirmSideEffects,
              })
            }
            onClick={() => void submitRerun()}
          >
            {pendingRequest ? "确认提交结果" : "确认重跑"}
          </Button>,
        ]}
        className="task-rerun-modal"
      >
        {shouldBlockTaskRerunFormForPreflight(loading, pendingRequest) ? (
          <div className="rerun-loading">
            <Spin />
            <span>正在检查重跑条件</span>
          </div>
        ) : (
          <div className="rerun-form">
            {error && <AntAlert type="error" showIcon title={error} />}
            {outcome && <AntAlert type="success" showIcon title={outcome} />}
            {pendingRequest && !outcome && (
              <AntAlert
                type="warning"
                showIcon
                title="存在待确认的重跑请求"
                description="上次提交结果尚未确认。系统将复用原幂等键和原请求参数，输入已锁定。"
                action={(
                  <Popconfirm
                    title="放弃待确认请求？"
                    description="原请求可能已经执行；放弃后再次重跑可能重复触发外部操作。"
                    onConfirm={() => void discardPendingRequest()}
                  >
                    <Button size="small" danger>放弃请求</Button>
                  </Popconfirm>
                )}
              />
            )}
            {preflight && (
              <>
                <div className="rerun-summary">
                  <div><span>下一次执行</span><strong>第 {preflight.next_attempt_no} 次</strong></div>
                  <div><span>依赖状态</span><strong>{preflight.will_wait_for_dependencies ? "等待依赖" : "已满足"}</strong></div>
                  <div><span>起始节点</span><strong>{preflight.start_node}</strong></div>
                </div>
                {!!preflight.issues.length && (
                  <section className="rerun-issues">
                    <h4>当前不可重跑</h4>
                    <ul>
                      {preflight.issues.map((issue) => (
                        <li key={issue.code}><code>{issue.code}</code><span>{issue.message}</span></li>
                      ))}
                    </ul>
                  </section>
                )}
                {!!preflight.side_effects.length && (
                  <section className="rerun-side-effects">
                    <h4>可能重复触发的外部操作</h4>
                    <div className="rerun-side-effect-list">
                      {preflight.side_effects.map((effect) => (
                        <div key={effect.tool_execution_id || `${effect.subtask_id}-${effect.tool_name}`}>
                          <strong>{effect.tool_name}</strong>
                          <span>{effect.tool_type} · {effect.success ? "上次成功" : "上次失败"}</span>
                          <small>参数字段：{effect.argument_keys.join("、") || "无"}</small>
                        </div>
                      ))}
                    </div>
                  </section>
                )}
              </>
            )}
            <label className="field">
              <span>重跑理由</span>
              <Input.TextArea
                rows={3}
                value={reason}
                maxLength={300}
                showCount
                disabled={Boolean(outcome || pendingRequest)}
                onChange={(event) => {
                  setReason(event.target.value)
                  setError("")
                }}
              />
            </label>
            <div className="rerun-mode-field">
              <span>执行方式</span>
              <Segmented
                value={executionMode}
                options={[
                  { label: "后台执行", value: "async" },
                  { label: "同步等待", value: "sync" },
                ]}
                disabled={Boolean(outcome || pendingRequest)}
                onChange={(value) => setExecutionMode(value as "sync" | "async")}
              />
            </div>
            {preflight?.requires_side_effect_confirmation && (
              <Checkbox
                checked={confirmSideEffects}
                disabled={Boolean(outcome || pendingRequest)}
                onChange={(event) => {
                  setConfirmSideEffects(event.target.checked)
                  setError("")
                }}
              >
                我已确认重跑可能再次执行上述外部操作
              </Checkbox>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}

function readableRerunError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : fallback
  try {
    const detail = JSON.parse(message)
    if (Array.isArray(detail?.issues)) {
      return detail.issues
        .map((issue: { code?: string; message?: string }) => issue.message || issue.code)
        .filter(Boolean)
        .join("；") || fallback
    }
  } catch {
    return message
  }
  return message
}

function TaskInterventionPanel({ task, onTaskUpdated }: { task: Task; onTaskUpdated: (task: Task) => void }) {
  const [output, setOutput] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const needsIntervention = taskStatus(task) === "running" && task.current_node === "human_intervention"
  const intervention = taskInterventionView(task)

  useEffect(() => {
    if (needsIntervention) {
      setOutput(intervention.awaitingAcceptance ? "" : task.final_output || "")
    }
  }, [needsIntervention, intervention.awaitingAcceptance, task.id, task.final_output])

  if (!needsIntervention) return null

  async function submit(decision: "succeeded" | "failed" = "succeeded") {
    const value = output.trim()
    if (intervention.requiresOutput && !value) {
      setError("请填写处理结论")
      return
    }
    setSubmitting(true)
    setError("")
    try {
      const updated = await submitTaskResult(
        task.id,
        buildTaskInterventionResultPayload(task, value, decision),
      )
      onTaskUpdated(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交处理结果失败")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="task-intervention-panel">
      <header>
        <div>
          <ShieldCheck size={18} />
          <h4>{intervention.title}</h4>
        </div>
        <Tag color="orange">
          {intervention.awaitingAdjudication ? "待裁决" : intervention.awaitingAcceptance ? "待验收" : "待处理"}
        </Tag>
      </header>
      <p>{intervention.description}</p>
      <span className="task-intervention-label">{intervention.inputLabel}</span>
      <Input.TextArea
        rows={4}
        value={output}
        onChange={(event) => setOutput(event.target.value)}
        placeholder={intervention.placeholder}
      />
      {error && <AntAlert type="error" showIcon title={error} />}
      <div className="form-actions">
        <Button
          type="primary"
          icon={<CheckCircle2 size={16} />}
          onClick={() => void submit("succeeded")}
          loading={submitting}
          disabled={intervention.requiresOutput && !output.trim()}
        >
          {intervention.submitText}
        </Button>
        {intervention.awaitingAdjudication && (
          <Button
            danger
            icon={<XCircle size={16} />}
            onClick={() => void submit("failed")}
            disabled={submitting || !output.trim()}
          >
            判定失败
          </Button>
        )}
      </div>
    </section>
  )
}

function TaskResultDetail({ task }: { task: Task }) {
  const resultText = taskResultText(task)
  const artifacts = taskArtifactViews(task)
  const deliverableResults = taskDeliverableResultViews(task)
  return (
    <section className="task-result-detail">
      <header>
        <div>
          <FileText size={18} />
          <h4>产出成果</h4>
        </div>
        <Tag color={taskStatus(task) === "succeeded" ? "green" : taskStatus(task) === "failed" ? "red" : "blue"}>
          {taskStatusText(taskStatus(task))}
        </Tag>
      </header>
      <div className={resultText ? "task-result-text" : "task-result-text empty"}>
        {resultText || "任务还没有形成最终成果，执行中的节点输出会先沉淀到下方上下文。"}
      </div>
      {!!deliverableResults.length && (
        <div className="task-deliverable-list">
          {deliverableResults.map((result) => {
            const requirement = task.contract?.deliverable_requirements.find(
              (item) => item.id === result.requirementId,
            )
            return (
              <div className="task-deliverable-item" key={result.requirementId}>
                <div>
                  <strong>{requirement?.description || result.requirementId}</strong>
                  <span>{result.reason || "暂无判定说明"}</span>
                </div>
                <Tag color={criterionStatusColor(result.status)}>{criterionStatusText(result.status)}</Tag>
                {!!result.artifactIds.length && (
                  <small>关联产物：{result.artifactIds.join("、")}</small>
                )}
              </div>
            )
          })}
        </div>
      )}
      {!!artifacts.length && <ArtifactOutputList artifacts={artifacts} />}
    </section>
  )
}

function ArtifactOutputList({
  artifacts,
}: {
  artifacts: ReturnType<typeof taskArtifactViews>
}) {
  return (
    <div className="task-artifact-list">
      {artifacts.map((artifact) => {
        const clickableUri = taskArtifactClickableUri(artifact)
        return (
          <div className="task-artifact-item" key={artifact.id}>
            <div className="task-artifact-heading">
              <strong>{artifact.name || artifact.id}</strong>
              <span>
                <Tag>{artifactKindText(artifact.kind)}</Tag>
                <Tag color={artifactValidationColor(artifact.validationStatus)}>
                  {artifactValidationText(artifact.validationStatus)}
                </Tag>
              </span>
            </div>
            {clickableUri ? (
              <a href={clickableUri} target="_blank" rel="noreferrer">{artifact.uri}</a>
            ) : artifact.uri ? (
              <span className="task-artifact-uri">{artifact.uri}</span>
            ) : (
              <p>{artifact.contentPreview || "暂无文本预览"}</p>
            )}
            {artifact.validationReason && <small>{artifact.validationReason}</small>}
          </div>
        )
      })}
    </div>
  )
}

function ExecutionHistory({ task }: { task: Task }) {
  const executions = taskExecutionHistory(task)
  const activeExecutionId = executions.find((execution) => execution.isActive)?.id || ""
  const [activeKeys, setActiveKeys] = useState<string[]>(
    () => executionHistoryActiveKeys([], activeExecutionId),
  )

  useEffect(() => {
    setActiveKeys((current) => executionHistoryActiveKeys(current, activeExecutionId))
  }, [activeExecutionId])

  return (
    <section className="task-execution-history">
      <header>
        <div>
          <RefreshCw size={18} />
          <h4>执行历史</h4>
        </div>
        <Tag>{executions.length} 次</Tag>
      </header>
      {executions.length ? (
        <Collapse
          ghost
          activeKey={activeKeys}
          onChange={(keys) => setActiveKeys((Array.isArray(keys) ? keys : [keys]).map(String))}
          items={executions.map((execution) => ({
            key: execution.id,
            label: (
              <div className="execution-history-label">
                <strong>第 {execution.attemptNo} 次</strong>
                <Tag color={execution.trigger === "rerun" ? "purple" : "blue"}>
                  {execution.trigger === "rerun" ? "重跑" : "初次执行"}
                </Tag>
                <Tag color={taskStatusColor(execution.status)}>{taskStatusText(execution.status)}</Tag>
                {execution.isActive && <Tag color="cyan">当前</Tag>}
                <span>{execution.actor} · {formatDate(execution.time.createdAt)}</span>
              </div>
            ),
            children: (
              <div className="execution-history-detail">
                <dl>
                  <div><dt>触发原因</dt><dd>{execution.triggerReason || "-"}</dd></div>
                  <div><dt>结束依据</dt><dd>{execution.status === "running" ? "尚未结束" : execution.reason || "-"}</dd></div>
                  <div><dt>开始时间</dt><dd>{formatDate(execution.time.startedAt || "")}</dd></div>
                  <div><dt>结束时间</dt><dd>{formatDate(execution.time.finishedAt || "")}</dd></div>
                </dl>
                {execution.report && (
                  <section className="execution-report-detail">
                    <h5>完成报告</h5>
                    <p>{execution.report.completionReason || "未记录完成原因"}</p>
                    {execution.report.evidenceSummary && <small>{execution.report.evidenceSummary}</small>}
                    <div className="execution-report-meta">
                      <span>人工验收：{taskHumanAcceptanceText(execution.report)}</span>
                      <span>
                        判定人：{execution.report.decidedById || execution.report.decidedByType || "-"}
                      </span>
                      <span>判定时间：{formatDate(execution.report.decidedAt)}</span>
                    </div>
                    {!!execution.report.criterionResults.length && (
                      <ul className="criterion-result-list">
                        {execution.report.criterionResults.map((result) => (
                          <li key={result.criterionId}>
                            <strong>{result.criterionId}</strong>
                            <span>{criterionStatusText(result.status)}</span>
                            {!!result.evidenceArtifactIds.length && (
                              <small>证据产物：{result.evidenceArtifactIds.join("、")}</small>
                            )}
                            {result.evidenceText && <small>证据说明：{result.evidenceText}</small>}
                            {result.reason && <small>判定原因：{result.reason}</small>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {!!execution.report.deliverableResults.length && (
                      <ul>
                        {execution.report.deliverableResults.map((result) => (
                          <li key={result.requirementId}>
                            <strong>{result.requirementId}</strong>
                            <span>{criterionStatusText(result.status)}{result.reason ? ` · ${result.reason}` : ""}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                )}
                {!!execution.artifacts.length && <ArtifactOutputList artifacts={execution.artifacts} />}
              </div>
            ),
          }))}
        />
      ) : (
        <EmptyState text="暂无执行历史" />
      )}
    </section>
  )
}

function criterionStatusText(status: string) {
  return { passed: "通过", failed: "未通过", pending: "待确认" }[status] || status
}

function criterionStatusColor(status: string) {
  return { passed: "green", failed: "red", pending: "gold" }[status] || "default"
}

function artifactKindText(kind: string) {
  return { text: "文本", file: "文件", tool_result: "工具结果" }[kind] || kind
}

function artifactValidationText(status: string) {
  return { valid: "有效", invalid: "无效", pending: "待校验" }[status] || status
}

function artifactValidationColor(status: string) {
  return { valid: "green", invalid: "red", pending: "gold" }[status] || "default"
}

function TaskAttachmentDetail({ attachments }: { attachments: TaskAttachment[] }) {
  return (
    <section className="task-attachment-detail">
      <div>
        <h4>文本附件</h4>
        <span>{attachments.length} 个附件已解析进任务上下文</span>
      </div>
      <div className="task-attachment-chips">
        {attachments.map((attachment) => (
          <Tooltip key={attachment.id} title={attachment.text_preview || attachment.filename}>
            <Tag color={attachment.status === "parsed" ? "cyan" : "red"}>
              {attachment.filename} · {attachment.text_length || 0} 字符
            </Tag>
          </Tooltip>
        ))}
      </div>
    </section>
  )
}

function TaskContextDetail({ task }: { task: Task }) {
  const rounds = task.context?.rounds || []
  const summary = String(task.context?.summary || "").trim()
  return (
    <section className="task-context-detail">
      <header>
        <div>
          <ListChecks size={18} />
          <h4>上下文与节点输出</h4>
        </div>
        <Tag color="cyan">{rounds.length} 轮</Tag>
      </header>
      {summary ? (
        <details className="context-summary-card">
          <summary>
            <span>当前上下文汇总</span>
            <small>{compactContextText(summary, 110)}</small>
          </summary>
          <pre>{summary}</pre>
        </details>
      ) : (
        <div className="context-empty">暂无上下文沉淀，任务执行后会在这里展示每个节点输出。</div>
      )}
      {!!rounds.length && (
        <div className="context-round-list">
          {rounds.map((round) => (
            <details className="context-round-card" key={round.id || round.round_index} open>
              <summary>
                <strong>第 {round.round_index ?? "-"} 轮</strong>
                <span>{round.execution_mode || "unknown"} · {round.subtasks?.length || 0} 个节点</span>
              </summary>
              {round.reason && <p className="context-round-reason">{round.reason}</p>}
              <div className="context-subtask-list">
                {(round.subtasks || []).map((subtask) => {
                  const nodeView = taskContextNodeView(subtask)
                  return (
                    <details className="context-subtask-card" key={subtask.id}>
                      <summary className="context-subtask-summary">
                        <span className={`context-node-dot ${subtask.status || "running"}`} />
                        <span className="context-node-main">
                          <span className="context-node-title-row">
                            <strong>{nodeView.title}</strong>
                            <span className="context-node-tags">
                              <Tag>{nodeView.typeText}</Tag>
                              <Tag color={subtask.status === "succeeded" ? "green" : subtask.status === "failed" ? "red" : "blue"}>{taskStatusText(subtask.status)}</Tag>
                            </span>
                          </span>
                          <span className="context-node-preview">{nodeView.preview}</span>
                        </span>
                      </summary>
                      <div className="context-subtask-detail">
                        {nodeView.assigneeText && <small>执行主体：{nodeView.assigneeText}</small>}
                        <section>
                          <span>节点描述</span>
                          <p>{subtask.description || "暂无节点说明"}</p>
                        </section>
                        <section>
                          <span>节点输出</span>
                          <pre className={subtask.output ? "" : "empty"}>{subtask.output || "暂无输出"}</pre>
                        </section>
                        {!!subtask.tool_results?.length && (
                          <details className="tool-result-detail">
                            <summary>工具调用结果 {subtask.tool_results.length} 条</summary>
                            {subtask.tool_results.map((result, index) => (
                              <pre key={index}>{displayValue(result)}</pre>
                            ))}
                          </details>
                        )}
                      </div>
                    </details>
                  )
                })}
              </div>
              {(round.context_before || round.context_after) && (
                <details className="round-context-panel">
                  <summary>轮次上下文</summary>
                  <div className="round-context-diff">
                    <details>
                      <summary>执行前上下文</summary>
                      <pre>{round.context_before || "空"}</pre>
                    </details>
                    <details>
                      <summary>执行后上下文</summary>
                      <pre>{round.context_after || "空"}</pre>
                    </details>
                  </div>
                </details>
              )}
            </details>
          ))}
        </div>
      )}
    </section>
  )
}

const taskDetailWorkflowNodeTypes = { workflowNode: TaskDetailWorkflowNode }

function ManualWorkflowDetail({ task, definition }: { task: Task; definition: WorkflowDefinition }) {
  const flow = useMemo(() => manualWorkflowFlowElements(task, definition), [task, definition])

  return (
    <div className="manual-workflow-detail">
      <div className="manual-workflow-stats">
        <Tag color="cyan">节点 {definition.nodes.length}</Tag>
        <Tag color="geekblue">连线 {definition.edges.length}</Tag>
        <span>{task.request_metadata?.workflow_name || task.title || "手动编排流程"}</span>
      </div>
      <div className="manual-workflow-flow" aria-label="手动编排流程图">
        <ReactFlow
          nodes={flow.nodes}
          edges={flow.edges}
          nodeTypes={taskDetailWorkflowNodeTypes}
          defaultViewport={{ x: 20, y: 24, zoom: 0.88 }}
          fitView
          fitViewOptions={{ padding: 0.18, maxZoom: 0.95 }}
          minZoom={0.45}
          maxZoom={1.6}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag
          zoomOnScroll
          zoomOnPinch
          preventScrolling={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#b7c4d6" gap={18} size={1.1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}

function TaskDetailWorkflowNode({ data }: NodeProps<WorkflowReactFlowNode>) {
  const kind = String(data.kind || "")
  const status = String(data.status || "pending")
  const output = String(data.output || "")
  const assignee = String(data.assigneeUserName || data.assignee || "")
  return (
    <div className={`task-detail-workflow-node ${kind} ${status}`}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="task-detail-workflow-node-head">
        <span className={kind === "human" ? "task-detail-workflow-icon warning" : kind === "end" ? "task-detail-workflow-icon success" : "task-detail-workflow-icon"}>
          {taskDetailWorkflowNodeIcon(kind, String(data.id || ""))}
        </span>
        <div>
          <strong>{String(data.title || data.id || "节点")}</strong>
          <span>
            <Tag>{String(data.kindText || "节点")}</Tag>
            <Tag color={detailWorkflowNodeStateColor(status)}>{String(data.statusText || status)}</Tag>
          </span>
        </div>
      </div>
      <p>{String(data.description || "暂无节点说明")}</p>
      {assignee && <small>处理人：{assignee}</small>}
      {output && <small className="task-detail-workflow-output">输出：{output}</small>}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}

function taskDetailWorkflowNodeIcon(kind: string, id: string) {
  if (kind === "start") return <Sparkles size={16} />
  if (kind === "human") return <UserCheck size={16} />
  if (kind === "condition") return <GitBranch size={16} />
  if (kind === "end") return <CheckCircle2 size={16} />
  if (id.includes("review")) return <ClipboardCheck size={16} />
  return <Bot size={16} />
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
                const failureReason = subtaskFailureReason(subtask)
                const nodeContent = (
                  <>
                    <div className="subtask-node-title">{subtask.title || subtask.id}</div>
                    <div className="subtask-node-meta">
                      <span>{isHumanNode ? "人工节点" : "Agent节点"}</span>
                      <span className={`status-pill ${subtask.status}`}>{taskStatusText(subtask.status)}</span>
                    </div>
                    {subtask.output && <div className="subtask-node-output">{subtask.output}</div>}
                    {canOpenHumanWorkbench && <span className="subtask-node-action">点击处理</span>}
                  </>
                )
                const renderedNode = canOpenHumanWorkbench ? (
                  <button type="button" className={`graph-subtask-node clickable ${subtask.status || "running"}`} onClick={onOpenHumanWorkbench}>
                    {nodeContent}
                  </button>
                ) : (
                  <article className={`graph-subtask-node ${subtask.status || "running"}`}>{nodeContent}</article>
                )
                return (
                  <Tooltip key={subtask.id} title={failureReason} placement="top" overlayClassName="subtask-failure-tooltip">
                    {renderedNode}
                  </Tooltip>
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

function subtaskFailureReason(subtask: SubTask) {
  if (subtask.status !== "failed") return ""
  const failedTools = (subtask.tool_results || [])
    .filter((result) => !result.success)
    .map((result) => `${result.tool_name}: ${result.error || "工具执行失败"}`)
  const lines = [
    subtask.output ? `失败原因：${subtask.output}` : "",
    failedTools.length ? `工具错误：${failedTools.join("；")}` : "",
    subtask.description ? `子任务描述：${subtask.description}` : "",
  ].filter(Boolean)
  return lines.length ? lines.join("\n") : "子任务执行失败"
}

function AgentsPage({
  agents,
  users,
  setAgents,
  setToast,
}: {
  agents: Agent[]
  users: UserOption[]
  setAgents: (agents: Agent[] | ((current: Agent[]) => Agent[])) => void
  setToast: (value: string) => void
}) {
  const [nodeType, setNodeType] = useState("processing")
  const [description, setDescription] = useState("向指定目录写入文章或者报告总结")
  const [humanAssigneeUserId, setHumanAssigneeUserId] = useState("")
  const [name, setName] = useState("报告写入节点")
  const [result, setResult] = useState<{ status: string; message: string; agent?: Agent | null; guidance?: string[] } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [agentPage, setAgentPage] = useState(1)
  const selectedHumanAssignee = users.find((user) => user.id === humanAssigneeUserId)
  const agentPageSize = 20
  const pagedAgents = useMemo(
    () => agents.slice((agentPage - 1) * agentPageSize, agentPage * agentPageSize),
    [agentPage, agents],
  )

  useEffect(() => {
    if (!humanAssigneeUserId && users[0]) setHumanAssigneeUserId(users[0].id)
  }, [humanAssigneeUserId, users])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(agents.length / agentPageSize))
    if (agentPage > maxPage) setAgentPage(maxPage)
  }, [agentPage, agents.length])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      if (nodeType === "processing") {
        const response = await createSimpleAgent(description, name)
        setResult(response)
        if (response.agent) {
          setAgents((current) => [response.agent!, ...current])
          setToast("Agent 节点已创建")
        }
        return
      }
      if (nodeType === "human") {
        if (!selectedHumanAssignee) return
        const response = await createHumanNodeForUser(selectedHumanAssignee, name.trim())
        setResult(response)
        if (response.agent) {
          setAgents((current) => [response.agent!, ...current])
          setToast("人工节点已创建")
        }
        return
      }
      const response = await createAgent({
        name: name.trim(),
        description: description.trim(),
        agent_type: "condition",
        capabilities: ["condition_judge"],
      })
      setResult({ status: "created", message: "判断节点已创建", agent: response })
      setAgents((current) => [response, ...current])
      setToast("判断节点已创建")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page active">
      <PageHeader title="流程节点管理" description="创建和维护流程画布可用的 Agent 节点和人工节点。" />
      <div className="grid agents-management-grid">
        <Card className="form-panel" title="创建流程节点" size="small">
        <form onSubmit={submit}>
          <label className="field">
            <span>节点类型</span>
            <Select
              value={nodeType}
              options={[
                { value: "processing", label: "Agent 节点" },
                { value: "human", label: "人工节点" },
              ]}
              onChange={setNodeType}
            />
          </label>
          <label className="field">
            <span>节点名称</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          {nodeType === "human" ? (
            <label className="field">
              <span>操作人员</span>
              <Select
                showSearch
                value={humanAssigneeUserId || undefined}
                placeholder="请选择操作人员"
                optionFilterProp="label"
                options={users.map((user) => ({
                  value: user.id,
                  label: user.name,
                }))}
                onChange={setHumanAssigneeUserId}
              />
            </label>
          ) : (
            <label className="field">
              <span>{nodeType === "condition" ? "判断逻辑" : "节点说明"}</span>
              <Input.TextArea
                rows={6}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={nodeType === "condition" ? "请描述判断条件和输出规则" : "请描述节点能力"}
              />
            </label>
          )}
          <Button
            type="primary"
            htmlType="submit"
            icon={<Bot size={16} />}
            loading={submitting}
            disabled={!name.trim() || (nodeType === "human" && !selectedHumanAssignee)}
          >
            创建流程节点
          </Button>
          {result && (
            <AntAlert
              className="agent-result"
              type={result.agent ? "success" : "warning"}
              showIcon
              title={result.agent ? "节点已创建" : "节点未创建"}
              description={
                <>
                  {result.agent ? `${result.agent.name} 已作为${nodeTypeLabel(result.agent.agent_type)}保存` : result.message}
                  {result.guidance?.map((item) => <p key={item}>{item}</p>)}
                </>
              }
            />
          )}
        </form>
        </Card>
        <Panel title="已注册流程节点">
          {agents.length ? (
            <>
              <div className="registered-node-grid">
              {pagedAgents.map((agent) => {
                const capabilities = agent.capabilities || []
                const tools = agent.tools || []
                const toolTypes = Array.from(new Set(tools.map((tool) => tool.type).filter(Boolean)))
                const shownCapabilities = capabilities.slice(0, 3)
                const hiddenCapabilityCount = Math.max(capabilities.length - shownCapabilities.length, 0)
                const shownToolTypes = toolTypes.slice(0, 2)
                const hiddenToolTypeCount = Math.max(toolTypes.length - shownToolTypes.length, 0)

                return (
                  <article className={`registered-node-card ${agent.agent_type || "processing"}`} key={agent.id}>
                    <div className="registered-node-head">
                      <span className={`registered-node-icon ${agent.agent_type || "processing"}`}>{nodeTypeIcon(agent.agent_type)}</span>
                      <div className="registered-node-title-block">
                        <Tooltip title={agent.name}>
                          <strong className="registered-node-name">{agent.name || "未命名节点"}</strong>
                        </Tooltip>
                        <span>{nodeTypeLabel(agent.agent_type)}</span>
                      </div>
                    </div>
                    <Tooltip title={agent.description || "暂无节点说明"} placement="topLeft">
                      <p className="registered-node-desc">{agent.description || "暂无节点说明"}</p>
                    </Tooltip>
                    <Flex className="registered-node-tags" wrap gap={6}>
                      <Tag color={nodeTypeColor(agent.agent_type)}>{nodeTypeLabel(agent.agent_type)}</Tag>
                      {shownCapabilities.map((capability) => <Tag key={capability}>{capability}</Tag>)}
                      {hiddenCapabilityCount > 0 && <Tag>+{hiddenCapabilityCount}</Tag>}
                      {shownToolTypes.map((toolType) => <Tag color="blue" key={toolType}>{toolType}</Tag>)}
                      {hiddenToolTypeCount > 0 && <Tag color="blue">+{hiddenToolTypeCount} 工具</Tag>}
                      {!toolTypes.length && <Tag color="default">无工具</Tag>}
                    </Flex>
                  </article>
                )
              })}
              </div>
              {agents.length > agentPageSize && (
                <div className="registered-node-pagination">
                  <Pagination
                    current={agentPage}
                    pageSize={agentPageSize}
                    total={agents.length}
                    showSizeChanger={false}
                    showTotal={(total, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 个节点`}
                    onChange={setAgentPage}
                  />
                </div>
              )}
            </>
          ) : (
            <EmptyState text="暂无流程节点" />
          )}
        </Panel>
      </div>
    </div>
  )
}

const emptyUserForm: UserCreatePayload = {
  name: "",
  phone: "",
  email: "",
  role: "user",
  department: "",
  position: "",
  status: "active",
  remark: "",
}

function UsersPage({
  users,
  setUsers,
  setToast,
}: {
  users: User[]
  setUsers: (users: User[] | ((current: User[]) => User[])) => void
  setToast: (value: string) => void
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [form, setForm] = useState<UserCreatePayload>(emptyUserForm)
  const [submitting, setSubmitting] = useState(false)

  function openCreate() {
    setEditingUser(null)
    setForm(emptyUserForm)
    setModalOpen(true)
  }

  function openEdit(user: User) {
    setEditingUser(user)
    setForm({
      name: user.name,
      phone: user.phone || "",
      email: user.email || "",
      role: user.role,
      department: user.department || "",
      position: user.position || "",
      status: user.status || "active",
      remark: user.remark || "",
    })
    setModalOpen(true)
  }

  function patchForm(patch: Partial<UserCreatePayload>) {
    setForm((current) => ({ ...current, ...patch }))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      if (editingUser) {
        const updated = await updateUser(editingUser.id, form)
        setUsers((current) => current.map((user) => user.id === updated.id ? updated : user))
        setToast("用户已更新")
      } else {
        const created = await createUser(form)
        setUsers((current) => [created, ...current])
        setToast("用户已新增")
      }
      setModalOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  async function remove(user: User) {
    await deleteUser(user.id)
    setUsers((current) => current.filter((item) => item.id !== user.id))
    setToast("用户已删除")
  }

  const columns: ColumnsType<User> = [
    { title: "姓名", dataIndex: "name", ellipsis: true },
    { title: "手机号", dataIndex: "phone", ellipsis: true, render: (value: string) => value || "-" },
    { title: "邮箱", dataIndex: "email", ellipsis: true, render: (value: string) => value || "-" },
    { title: "角色", dataIndex: "role", width: 110, render: (value: string) => <Tag color={value === "admin" ? "gold" : "blue"}>{roleLabel(value)}</Tag> },
    { title: "部门", dataIndex: "department", ellipsis: true, render: (value: string) => value || "-" },
    { title: "岗位", dataIndex: "position", ellipsis: true, render: (value: string) => value || "-" },
    { title: "状态", dataIndex: "status", width: 100, render: (value: string) => <Tag color={value === "active" ? "success" : "default"}>{userStatusLabel(value)}</Tag> },
    {
      title: "操作",
      width: 150,
      render: (_, user) => (
        <Space>
          <Button size="small" icon={<Edit3 size={14} />} onClick={() => openEdit(user)}>编辑</Button>
          <Popconfirm title="确认删除该用户？" onConfirm={() => void remove(user)} okText="删除" cancelText="取消">
            <Button size="small" danger icon={<Trash2 size={14} />} disabled={user.id === "root"}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="page active">
      <PageHeader title="用户管理" description="维护系统用户、角色和基础联系方式，人工节点按人员姓名分配。">
        <Button type="primary" icon={<Plus size={16} />} onClick={openCreate}>
          新增用户
        </Button>
      </PageHeader>
      <Panel title="用户列表">
        <Table rowKey="id" columns={columns} dataSource={users} pagination={false} />
      </Panel>
      <Modal
        title={editingUser ? "编辑用户" : "新增用户"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <form className="user-form" onSubmit={submit}>
          <label className="field">
            <span>姓名</span>
            <Input value={form.name} onChange={(event) => patchForm({ name: event.target.value })} placeholder="请输入姓名" />
          </label>
          <div className="form-grid two">
            <label className="field">
              <span>手机号</span>
              <Input value={form.phone} onChange={(event) => patchForm({ phone: event.target.value })} placeholder="请输入手机号" />
            </label>
            <label className="field">
              <span>邮箱</span>
              <Input value={form.email} onChange={(event) => patchForm({ email: event.target.value })} placeholder="请输入邮箱" />
            </label>
          </div>
          <div className="form-grid two">
            <label className="field">
              <span>角色</span>
              <Select
                value={form.role}
                options={[
                  { value: "admin", label: "管理员" },
                  { value: "user", label: "普通用户" },
                ]}
                onChange={(value) => patchForm({ role: value })}
              />
            </label>
            <label className="field">
              <span>状态</span>
              <Select
                value={form.status}
                options={[
                  { value: "active", label: "启用" },
                  { value: "disabled", label: "停用" },
                ]}
                onChange={(value) => patchForm({ status: value })}
              />
            </label>
          </div>
          <div className="form-grid two">
            <label className="field">
              <span>部门</span>
              <Input value={form.department} onChange={(event) => patchForm({ department: event.target.value })} placeholder="请输入部门" />
            </label>
            <label className="field">
              <span>岗位</span>
              <Input value={form.position} onChange={(event) => patchForm({ position: event.target.value })} placeholder="请输入岗位" />
            </label>
          </div>
          <label className="field">
            <span>备注</span>
            <Input.TextArea rows={3} value={form.remark} onChange={(event) => patchForm({ remark: event.target.value })} placeholder="可填写职责范围或说明" />
          </label>
          <div className="form-actions">
            <Button onClick={() => setModalOpen(false)} disabled={submitting}>取消</Button>
            <Button type="primary" htmlType="submit" loading={submitting} disabled={!form.name?.trim()}>
              保存
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}

function nodeTypeLabel(type?: string) {
  return { processing: "Agent 节点", human: "人工节点", condition: "判断节点" }[type || "processing"] || type || "Agent 节点"
}

function nodeTypeColor(type?: string) {
  return { processing: "blue", human: "green", condition: "purple" }[type || "processing"] || "default"
}

function nodeTypeIcon(type?: string) {
  if (type === "human") return <UserCheck size={18} />
  if (type === "condition") return <GitBranch size={18} />
  return <Bot size={18} />
}

function roleLabel(role?: string) {
  return { admin: "管理员", user: "普通用户" }[role || "user"] || role || "普通用户"
}

function userStatusLabel(status?: string) {
  return { active: "启用", disabled: "停用" }[status || "active"] || status || "启用"
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
      <Statistic title={label} value={value} styles={{ content: { color: toneColor(tone), fontSize: 26 } }} />
    </Card>
  )
}

function TaskTable({
  tasks,
  compact,
  onSelect,
  onContinueConfirmation,
  onTaskUpdated,
  selectedTaskId,
}: {
  tasks: Task[]
  compact?: boolean
  onSelect?: (id: string) => void
  onContinueConfirmation?: (task: Task) => void
  onTaskUpdated?: (task: Task) => void
  selectedTaskId?: string
}) {
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
      title: "任务类型",
      dataIndex: "task_type",
      width: 120,
      render: (_: unknown, task: Task) => <Tag color={taskTypeColor(task)}>{taskTypeText(task)}</Tag>,
    } as ColumnsType<Task>[number]] : []),
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
      render: (_: unknown, task: Task) => taskNodeText(task),
    } as ColumnsType<Task>[number]] : []),
    {
      title: "状态",
      width: 110,
      render: (_, task) => <Tag color={taskStatusColor(taskStatus(task))}>{taskStatusText(taskStatus(task))}</Tag>,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 130,
      render: (value: string) => formatDate(value),
    },
    ...(onSelect ? [{
      title: "操作",
      width: 190,
      render: (_: unknown, task: Task) => (
        <Space size={4}>
          <Button size="small" onClick={() => onSelect(task.id)}>详情</Button>
          {onContinueConfirmation && isTaskAwaitingConfirmation(task) && (
            <Button
              size="small"
              type="primary"
              icon={<ClipboardCheck size={14} />}
              onClick={() => onContinueConfirmation(task)}
            >
              继续确认
            </Button>
          )}
          {onTaskUpdated && isTaskRerunnable(task) && (
            <TaskRerunControl task={task} onTaskUpdated={onTaskUpdated} />
          )}
        </Space>
      ),
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
