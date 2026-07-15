export type TaskStatus = "running" | "succeeded" | "failed"

export interface AgentTool {
  name: string
  description?: string
  type: string
  config?: Record<string, string>
  input_schema?: Record<string, unknown>
}

export interface Agent {
  id: string
  name: string
  description?: string
  agent_type?: string
  capabilities?: string[]
  tools?: AgentTool[]
  created_at?: string
}

export interface TaskEvent {
  id?: string | number
  task_id?: string
  subtask_id?: string
  event_type?: string
  message?: string
  created_at?: string
}

export interface SubTask {
  id: string
  task_id?: string
  title?: string
  description?: string
  status?: TaskStatus
  current_node?: string
  assignee_type?: string
  assigned_agent_id?: string
  output?: string
  error_message?: string
  tool_results?: Array<Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

export interface TaskRound {
  id?: string
  round_index?: number
  execution_mode?: string
  reason?: string
  subtasks?: SubTask[]
}

export interface Task {
  id: string
  title?: string
  description?: string
  content?: string
  source_type?: string
  task_status?: TaskStatus
  status?: TaskStatus
  current_node?: string
  assigned_agent_id?: string
  loop_count?: number
  max_loop_count?: number
  context?: {
    summary?: string
    rounds?: TaskRound[]
    artifacts?: unknown[]
  }
  draft?: {
    title?: string
    description?: string
  }
  events?: TaskEvent[]
  final_output?: string
  created_at?: string
  updated_at?: string
}

export interface TaskRequestResponse {
  request_id?: string
  tasks: Task[]
}

export interface SimpleAgentResponse {
  status: "created" | "ready" | "needs_split" | "tool_missing"
  message: string
  agent: Agent | null
  matched_tools: string[]
  missing_tools: Array<{ type: string; reason: string; suggested_action?: string }>
  guidance: string[]
}

const configuredBaseUrl = import.meta.env.VITE_TASKHUB_API_BASE_URL as string | undefined
const apiBaseUrl = (configuredBaseUrl || "").replace(/\/+$/, "")

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    const detail = payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || JSON.stringify(item)).join("；")
      : detail || payload?.message || `接口请求失败：${response.status}`
    throw new Error(message)
  }
  return payload as T
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  })
  return readJson<T>(response)
}

export function listTasks() {
  return request<Task[]>("/api/v1/tasks")
}

export function getTask(taskId: string) {
  return request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`)
}

export function createTaskRequest(content: string, sourceType = "business_system") {
  return request<TaskRequestResponse>("/api/v1/tasks/requests", {
    method: "POST",
    body: JSON.stringify({
      source_type: sourceType,
      content,
      metadata: {},
    }),
  })
}

export function confirmTask(taskId: string, payload: { title: string; description: string; execution_mode?: "sync" | "async" }) {
  return request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function listHumanSubtasks() {
  return request<SubTask[]>("/api/v1/subtasks/human")
}

export function submitHumanSubtaskResult(subtaskId: string, payload: { status: string; output: string; metadata?: Record<string, string> }) {
  return request<Task>(`/api/v1/subtasks/${encodeURIComponent(subtaskId)}/result`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function listAgents() {
  return request<Agent[]>("/api/v1/agents")
}

export function createSimpleAgent(ability: string, name?: string) {
  return request<SimpleAgentResponse>("/api/v1/agents/simple", {
    method: "POST",
    body: JSON.stringify({ ability, name }),
  })
}

export function listWorkflows() {
  return request<Array<Record<string, unknown>>>("/api/v1/workflows")
}
