export type PageId = "overview" | "publish" | "confirmation" | "tasks" | "workflows" | "agents" | "users"

export type PageRefreshTarget = "tasks" | "humanSubtasks" | "agents" | "assignableUsers" | "workflows" | "users"

const ADMIN_ONLY_PAGES = new Set<PageId>(["workflows", "agents", "users"])

export function canNavigateToPage(page: PageId, isAdmin: boolean) {
  return isAdmin || !ADMIN_ONLY_PAGES.has(page)
}

export function refreshTargetsForPage(page: PageId, isAdmin: boolean): PageRefreshTarget[] {
  if (!canNavigateToPage(page, isAdmin)) return []
  if (page === "overview") return ["tasks", "humanSubtasks", "agents"]
  if (page === "tasks") return ["tasks"]
  if (page === "confirmation") return ["humanSubtasks"]
  if (page === "workflows") return ["agents", "assignableUsers", "workflows"]
  if (page === "agents") return ["agents", "assignableUsers"]
  if (page === "users") return ["users", "assignableUsers"]
  return []
}
