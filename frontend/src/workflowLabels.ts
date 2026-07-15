const CAPABILITY_LABELS: Record<string, string> = {
  all: "全部能力",
  analysis: "数据分析",
  contract: "合同审查",
  crm: "客户管理",
  data: "数据处理",
  email: "邮件处理",
  general_processing: "通用处理",
  legal: "法务合规",
  notification: "通知提醒",
  quote: "报价处理",
  report: "报表分析",
  risk: "风险识别",
  sales: "销售协同",
  save_file: "保存文件",
  send_email: "发送邮件",
  summarize: "内容总结",
  write_article: "文章撰写",
  write_report: "报告撰写",
}

export function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] || capability.replace(/_/g, " ")
}
