export type TaskStatus = "running" | "succeeded" | "failed"
export type TaskType = "auto_planning" | "manual_orchestration"

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
  metadata?: Record<string, string>
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
  task_title?: string
  task_description?: string
  task_content?: string
  task_context_summary?: string
  task_artifacts?: unknown[]
  upstream_outputs?: string[]
  title?: string
  description?: string
  status?: TaskStatus
  current_node?: string
  assignee_type?: string
  assigned_agent_id?: string
  assignee_user_id?: string
  assignee_user_name?: string
  assignee_role?: string
  output?: string
  error_message?: string
  tool_results?: Array<Record<string, unknown>>
  result_metadata?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export interface TaskRound {
  id?: string
  round_index?: number
  execution_mode?: string
  reason?: string
  context_before?: string
  subtasks?: SubTask[]
  context_after?: string
}

export interface Task {
  id: string
  title?: string
  description?: string
  content?: string
  task_type?: TaskType
  source_type?: string
  request_metadata?: Record<string, unknown> & {
    execution_mode?: string
    workflow_name?: string
    workflow_description?: string
    workflow_definition?: WorkflowDefinition
    attachment_ids?: string[]
    attachments?: TaskAttachment[]
  }
  created_by_user_id?: string
  created_by_user_name?: string
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

export interface TaskConfirmPayload {
  title: string
  description: string
  execution_mode?: "sync" | "async"
  default_assignee_user_id?: string
  default_assignee_user_name?: string
  default_assignee_role?: string
}

export interface TaskAttachment {
  id: string
  filename: string
  content_type?: string
  extension: string
  size_bytes: number
  text_preview?: string
  text_length?: number
  truncated?: boolean
  status?: string
  error?: string
  created_at?: string
}

export interface WorkflowNode {
  id: string
  type: string
  title?: string
  description?: string
  agent_id?: string | null
  config?: Record<string, unknown>
}

export interface WorkflowEdge {
  from: string
  to: string
  condition?: Record<string, unknown>
}

export interface WorkflowDefinition {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface WorkflowTemplate {
  id: string
  name: string
  description?: string
  definition: WorkflowDefinition
  status?: string
  created_at?: string
  updated_at?: string
}

export interface WorkflowCreatePayload {
  name: string
  description?: string
  definition: WorkflowDefinition
}

export interface WorkflowTaskMetadata {
  execution_mode: "workflow_template"
  workflow_id?: string
  workflow_name?: string
  workflow_description?: string
  workflow_definition?: WorkflowDefinition
}

export interface SimpleAgentResponse {
  status: "created" | "ready" | "needs_split" | "tool_missing" | "assignee_missing"
  message: string
  agent: Agent | null
  matched_tools: string[]
  missing_tools: Array<{ type: string; reason: string; suggested_action?: string }>
  guidance: string[]
}

export interface AgentCreatePayload {
  name: string
  description?: string
  agent_type?: string
  capabilities?: string[]
  metadata?: Record<string, string>
}

export type UserRole = "admin" | "user"

export interface User {
  id: string
  name: string
  phone?: string
  email?: string
  role: UserRole
  department?: string
  position?: string
  status?: string
  remark?: string
  created_at?: string
  updated_at?: string
}

export interface UserOption {
  id: string
  name: string
  role: UserRole
}

export interface UserCreatePayload {
  name: string
  phone?: string
  email?: string
  role?: UserRole
  department?: string
  position?: string
  status?: string
  remark?: string
}

export type UserUpdatePayload = Partial<UserCreatePayload>

const configuredBaseUrl = (import.meta as ImportMeta & { env?: { VITE_TASKHUB_API_BASE_URL?: string } }).env?.VITE_TASKHUB_API_BASE_URL
const apiBaseUrl = (configuredBaseUrl || "").replace(/\/+$/, "")
const currentUserStorageKey = "taskhub_current_user_id"
let activeUserId = readStoredUserId()

function readStoredUserId() {
  if (typeof globalThis.localStorage === "undefined") return ""
  return globalThis.localStorage.getItem(currentUserStorageKey) || ""
}

export function setCurrentUserId(userId: string) {
  activeUserId = userId
  if (typeof globalThis.localStorage === "undefined") return
  if (userId) {
    globalThis.localStorage.setItem(currentUserStorageKey, userId)
  } else {
    globalThis.localStorage.removeItem(currentUserStorageKey)
  }
}

export function getCurrentUserId() {
  return activeUserId || readStoredUserId()
}

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
  const userId = getCurrentUserId()
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(userId ? { "X-User-Id": userId } : {}),
      ...(options.body && !isFormData ? { "Content-Type": "application/json" } : {}),
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

export function buildTaskRequestPayload(
  title: string,
  content: string,
  workflow: string | WorkflowTaskMetadata = "",
  sourceType = "business_system",
  attachmentIds: string[] = [],
) {
  const workflowMetadata = typeof workflow === "string"
    ? workflow
      ? {
          execution_mode: "workflow_template",
          workflow_id: workflow,
        }
      : {}
    : workflow
  const metadata = attachmentIds.length
    ? {
        ...workflowMetadata,
        attachment_ids: attachmentIds,
      }
    : workflowMetadata
  return {
    source_type: sourceType,
    title,
    content,
    task_type: metadata.execution_mode === "workflow_template" ? "manual_orchestration" : "auto_planning",
    ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
    metadata,
  }
}

export function createTaskRequest(
  title: string,
  content: string,
  workflow: string | WorkflowTaskMetadata = "",
  sourceType = "business_system",
  attachmentIds: string[] = [],
) {
  return request<TaskRequestResponse>("/api/v1/tasks/requests", {
    method: "POST",
    body: JSON.stringify(buildTaskRequestPayload(title, content, workflow, sourceType, attachmentIds)),
  })
}

export function uploadTaskAttachment(file: File) {
  const formData = new FormData()
  formData.append("file", file)
  return request<TaskAttachment>("/api/v1/task-attachments", {
    method: "POST",
    body: formData,
  })
}

export function confirmTask(taskId: string, payload: TaskConfirmPayload) {
  return request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function cancelTask(taskId: string) {
  return request<void>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  })
}

export function listHumanSubtasks(assigneeUserId = "") {
  const query = assigneeUserId ? `?assignee_user_id=${encodeURIComponent(assigneeUserId)}` : ""
  return request<SubTask[]>(`/api/v1/subtasks/human${query}`)
}

export function submitHumanSubtaskResult(
  subtaskId: string,
  payload: {
    result_status: "succeeded" | "failed" | "blocked" | "partial"
    output: string
    should_complete?: boolean
    metadata?: Record<string, string>
    execution_mode?: "sync" | "async"
  },
) {
  return request<Task>(`/api/v1/subtasks/${encodeURIComponent(subtaskId)}/result`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function submitTaskResult(
  taskId: string,
  payload: {
    result_status: "succeeded" | "failed" | "blocked" | "partial"
    output: string
    should_complete?: boolean
    metadata?: Record<string, string>
    execution_mode?: "sync" | "async"
  },
) {
  return request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/result`, {
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

export function createHumanNode(assigneeUserName: string, name: string) {
  return request<SimpleAgentResponse>("/api/v1/agents/human-node", {
    method: "POST",
    body: JSON.stringify({ assignee_user_name: assigneeUserName, name }),
  })
}

export function createHumanNodeForUser(user: UserOption, name: string) {
  return request<SimpleAgentResponse>("/api/v1/agents/human-node", {
    method: "POST",
    body: JSON.stringify({
      assignee_user_id: user.id,
      assignee_user_name: user.name,
      assignee_role: user.role,
      name,
    }),
  })
}

export function createAgent(payload: AgentCreatePayload) {
  return request<Agent>("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function listWorkflows() {
  return request<WorkflowTemplate[]>("/api/v1/workflows")
}

export function createWorkflow(payload: WorkflowCreatePayload) {
  return request<WorkflowTemplate>("/api/v1/workflows", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function getCurrentUser() {
  return request<User>("/api/v1/users/current")
}

export function listUsers() {
  return request<User[]>("/api/v1/users")
}

export function listAssignableUsers() {
  return request<UserOption[]>("/api/v1/users/assignable")
}

export function createUser(payload: UserCreatePayload) {
  return request<User>("/api/v1/users", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function updateUser(userId: string, payload: UserUpdatePayload) {
  return request<User>(`/api/v1/users/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export function deleteUser(userId: string) {
  return request<void>(`/api/v1/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  })
}
