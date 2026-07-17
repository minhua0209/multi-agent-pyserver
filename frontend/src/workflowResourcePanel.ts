export function workflowResourcePanelClass(collapsed: boolean) {
  return collapsed ? "workflow-left-drawers collapsed" : "workflow-left-drawers"
}

export function workflowResourceToggleLabel(collapsed: boolean) {
  return collapsed ? "展开节点列表" : "收起节点列表"
}
