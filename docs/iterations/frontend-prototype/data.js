// =======================================
// API DATA CLIENT -- TaskHub front-end prototype
// 默认读取后端服务 http://192.168.170.18:8000/api/v1。
// 可通过 window.TASKHUB_API_BASE_URL 或 localStorage.TASKHUB_API_BASE_URL 覆盖。
// =======================================

const TASKHUB_ROLES = [
  { id: "publisher", name: "任务发布者", label: "发布者视角" },
  { id: "confirmer", name: "人工确认人", label: "确认人视角" },
  { id: "executor", name: "人工执行者", label: "执行者视角" },
  { id: "admin", name: "管理员", label: "管理员视角" },
  { id: "auditor", name: "运营/审计", label: "审计视角" },
]

const TASKHUB_GOVERNANCE = {
  runtimeMode: "接口联调模式",
  killSwitch: false,
  reservedRules: [
    { name: "低置信转人工", status: "预留", threshold: "confidence < 0.7" },
    { name: "循环超限转人工", status: "已由后端 max_loop_count 支撑", threshold: "loop_count >= 10" },
    { name: "工具失败转人工", status: "部分支撑", threshold: "tool_results.success = false" },
  ],
}

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "")
}

function resolveApiBaseUrl() {
  const configured = window.TASKHUB_API_BASE_URL || window.localStorage?.getItem("TASKHUB_API_BASE_URL")
  if (configured) return trimTrailingSlash(configured)
  return "http://127.0.0.1:8000"
}

const taskhubState = {
  apiBaseUrl: resolveApiBaseUrl(),
  loading: false,
  error: "",
  roles: TASKHUB_ROLES,
  agents: [],
  tasks: [],
  humanSubtasks: [],
  workflows: [],
  governance: TASKHUB_GOVERNANCE,
}

function apiUrl(path) {
  return `${taskhubState.apiBaseUrl}${path}`
}

async function readJsonResponse(response) {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch (error) {
    throw new Error(`接口返回非 JSON 内容：${text.slice(0, 120)}`)
  }
}

function apiErrorMessage(payload, fallback) {
  if (!payload) return fallback
  if (typeof payload.detail === "string") return payload.detail
  if (Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || JSON.stringify(item)).join("；")
  }
  return payload.message || fallback
}

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  })
  const payload = await readJsonResponse(response)
  if (!response.ok) {
    throw new Error(apiErrorMessage(payload, `接口请求失败：${response.status}`))
  }
  return payload
}

function normalizeAgent(agent) {
  return {
    ...agent,
    description: agent.description || "",
    capabilities: agent.capabilities || [],
    status: agent.status || "active",
    health: agent.health || "online",
    tools: agent.tools || [],
  }
}

function normalizeRound(round) {
  return {
    ...round,
    subtasks: (round.subtasks || []).map((subtask) => ({
      ...subtask,
      tool_calls: subtask.tool_calls || [],
      tool_results: subtask.tool_results || [],
    })),
  }
}

function normalizeTask(task) {
  const context = task.context || {}
  return {
    ...task,
    request_metadata: task.request_metadata || {},
    context: {
      summary: context.summary || "",
      rounds: (context.rounds || []).map(normalizeRound),
      artifacts: context.artifacts || [],
    },
    events: task.events || [],
    loop_count: task.loop_count ?? 0,
    max_loop_count: task.max_loop_count ?? 10,
  }
}

function replaceById(list, item) {
  const index = list.findIndex((current) => current.id === item.id)
  if (index >= 0) {
    list.splice(index, 1, item)
    return
  }
  list.unshift(item)
}

function setApiBaseUrl(value) {
  taskhubState.apiBaseUrl = trimTrailingSlash(value)
}

async function refreshTasks() {
  const tasks = await request("/api/v1/tasks")
  taskhubState.tasks = (tasks || []).map(normalizeTask)
  return taskhubState.tasks
}

async function refreshAgents() {
  const agents = await request("/api/v1/agents")
  taskhubState.agents = (agents || []).map(normalizeAgent)
  return taskhubState.agents
}

async function refreshHumanSubtasks() {
  const subtasks = await request("/api/v1/subtasks/human")
  taskhubState.humanSubtasks = subtasks || []
  return taskhubState.humanSubtasks
}

async function refreshWorkflows() {
  const workflows = await request("/api/v1/workflows")
  taskhubState.workflows = workflows || []
  return taskhubState.workflows
}

async function refreshAll() {
  taskhubState.loading = true
  taskhubState.error = ""
  try {
    const [tasks, agents, humanSubtasks, workflows] = await Promise.all([
      refreshTasks(),
      refreshAgents(),
      refreshHumanSubtasks(),
      refreshWorkflows(),
    ])
    return { tasks, agents, humanSubtasks, workflows }
  } catch (error) {
    taskhubState.error = error.message
    throw error
  } finally {
    taskhubState.loading = false
  }
}

async function getTask(taskId) {
  const task = normalizeTask(await request(`/api/v1/tasks/${encodeURIComponent(taskId)}`))
  replaceById(taskhubState.tasks, task)
  return task
}

async function createTaskRequest(payload) {
  const response = await request("/api/v1/tasks/requests", {
    method: "POST",
    body: JSON.stringify(payload),
  })
  const tasks = (response.tasks || []).map(normalizeTask)
  tasks.slice().reverse().forEach((task) => replaceById(taskhubState.tasks, task))
  return { ...response, tasks }
}

async function confirmTask(taskId, payload) {
  const task = normalizeTask(await request(`/api/v1/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  }))
  replaceById(taskhubState.tasks, task)
  return task
}

async function submitTaskResult(taskId, payload) {
  const task = normalizeTask(await request(`/api/v1/tasks/${encodeURIComponent(taskId)}/result`, {
    method: "POST",
    body: JSON.stringify(payload),
  }))
  replaceById(taskhubState.tasks, task)
  return task
}

async function createAgent(payload) {
  const agent = normalizeAgent(await request("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  }))
  replaceById(taskhubState.agents, agent)
  return agent
}

async function pollAgentTasks(agentId) {
  const tasks = await request(`/api/v1/agents/${encodeURIComponent(agentId)}/poll`, { method: "POST" })
  const normalizedTasks = (tasks || []).map(normalizeTask)
  normalizedTasks.slice().reverse().forEach((task) => replaceById(taskhubState.tasks, task))
  return normalizedTasks
}

async function submitHumanSubtaskResult(subtaskId, payload) {
  const task = normalizeTask(await request(`/api/v1/subtasks/${encodeURIComponent(subtaskId)}/result`, {
    method: "POST",
    body: JSON.stringify(payload),
  }))
  replaceById(taskhubState.tasks, task)
  await refreshHumanSubtasks()
  return task
}

function findAgent(agentId) {
  return taskhubState.agents.find((agent) => agent.id === agentId) || null
}

function findTask(taskId) {
  return taskhubState.tasks.find((task) => task.id === taskId) || null
}

function flattenEvents() {
  return taskhubState.tasks.flatMap((task) =>
    (task.events || []).map((event) => ({
      ...event,
      task_id: task.id,
      task_title: task.title || task.draft?.title || task.content,
    })),
  ).sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
}

window.TASKHUB_API = {
  get apiBaseUrl() {
    return taskhubState.apiBaseUrl
  },
  get loading() {
    return taskhubState.loading
  },
  get error() {
    return taskhubState.error
  },
  get roles() {
    return taskhubState.roles
  },
  get agents() {
    return taskhubState.agents
  },
  get tasks() {
    return taskhubState.tasks
  },
  get humanSubtasks() {
    return taskhubState.humanSubtasks
  },
  get workflows() {
    return taskhubState.workflows
  },
  get governance() {
    return taskhubState.governance
  },
  setApiBaseUrl,
  refreshAll,
  refreshTasks,
  refreshAgents,
  refreshHumanSubtasks,
  refreshWorkflows,
  getTask,
  createTaskRequest,
  confirmTask,
  submitTaskResult,
  createAgent,
  pollAgentTasks,
  submitHumanSubtaskResult,
  findAgent,
  findTask,
  flattenEvents,
}
