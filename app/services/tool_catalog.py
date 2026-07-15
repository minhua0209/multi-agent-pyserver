from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import AgentTool


@dataclass(frozen=True)
class ToolSpec:
    type: str
    name: str
    description: str
    capabilities: list[str]
    keywords: list[str]
    config_template: dict[str, str] = field(default_factory=dict)
    input_schema: dict = field(default_factory=dict)

    def to_agent_tool(self) -> AgentTool:
        return AgentTool(
            name=self.name,
            description=self.description,
            type=self.type,
            config=dict(self.config_template),
            input_schema=dict(self.input_schema),
        )


class ToolCatalog:
    def __init__(self) -> None:
        self._tools = [
            ToolSpec(
                type="file_write",
                name="file_write",
                description="将文章、报告、总结写入本地指定目录",
                capabilities=["write_article", "write_report", "summarize", "save_file"],
                keywords=["写入", "保存", "目录", "文件", "文章", "报告", "总结", "markdown", "md"],
                config_template={"base_dir": "./runtime/agent_outputs"},
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["filename", "content"],
                },
            ),
            ToolSpec(
                type="smtp_email",
                name="send_email",
                description="通过 SMTP 向指定邮箱发送邮件",
                capabilities=["send_email", "notify_user"],
                keywords=["邮件", "邮箱", "email", "发送通知"],
                config_template={
                    "smtp_host": "",
                    "smtp_port": "587",
                    "username": "",
                    "password": "",
                    "from": "",
                    "use_tls": "true",
                    "timeout_seconds": "30",
                },
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            ToolSpec(
                type="mysql",
                name="mysql_query",
                description="执行只读 MySQL SELECT 查询",
                capabilities=["query_mysql", "read_database"],
                keywords=["mysql", "数据库", "sql", "查询数据", "查库"],
                config_template={
                    "host": "127.0.0.1",
                    "port": "3306",
                    "user": "",
                    "password": "",
                    "database": "",
                    "query": "",
                    "max_rows": "50",
                },
            ),
            ToolSpec(
                type="http",
                name="http_request",
                description="调用外部 HTTP API",
                capabilities=["call_http_api", "call_business_system"],
                keywords=["http", "api", "接口", "调用外部", "业务系统"],
                config_template={"method": "GET", "url": ""},
            ),
        ]

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools)

    def match(self, ability: str) -> list[ToolSpec]:
        normalized = ability.lower()
        return [tool for tool in self._tools if any(keyword.lower() in normalized for keyword in tool.keywords)]
