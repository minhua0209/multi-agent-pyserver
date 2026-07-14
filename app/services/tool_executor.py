from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from urllib import request

import pymysql

from app.core.models import Agent, AgentTool, ToolCall, ToolExecutionResult


class ToolExecutor:
    def execute(self, agent: Agent, tool_call: ToolCall) -> ToolExecutionResult:
        tool = self._find_tool(agent, tool_call.tool_name)
        if tool is None:
            return ToolExecutionResult(
                tool_name=tool_call.tool_name,
                arguments=tool_call.arguments,
                success=False,
                error=f"Tool {tool_call.tool_name} is not registered for agent {agent.id}",
            )

        if tool.type == "mock":
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=True,
                result=tool.config.get("response", ""),
            )
        if tool.type == "http":
            return self._execute_http(tool, tool_call)
        if tool.type == "mysql":
            return self._execute_mysql(tool, tool_call)
        if tool.type == "smtp_email":
            return self._execute_smtp_email(tool, tool_call)

        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=tool_call.arguments,
            success=False,
            error=f"Tool type {tool.type} is not executable",
        )

    @staticmethod
    def _find_tool(agent: Agent, tool_name: str) -> AgentTool | None:
        return next((tool for tool in agent.tools if tool.name == tool_name), None)

    def _execute_http(self, tool: AgentTool, tool_call: ToolCall) -> ToolExecutionResult:
        method = tool.config.get("method", "GET").upper()
        url = self._format_url(tool.config.get("url", ""), tool_call.arguments)
        if not url:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error="HTTP tool url is empty",
            )

        headers = {"Content-Type": "application/json"}
        data = None
        if method not in {"GET", "DELETE"}:
            data = json.dumps(tool_call.arguments, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=20) as response:
                result = response.read().decode("utf-8")
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error=str(exc),
            )
        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=tool_call.arguments,
            success=True,
            result=result,
        )

    @staticmethod
    def _format_url(url: str, arguments: dict) -> str:
        formatted = url
        for key, value in arguments.items():
            formatted = formatted.replace("{" + str(key) + "}", str(value))
        return formatted

    def _execute_mysql(self, tool: AgentTool, tool_call: ToolCall) -> ToolExecutionResult:
        query = self._format_url(tool.config.get("query", ""), tool_call.arguments).strip()
        if not query:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error="MySQL tool query is empty",
            )
        if not query.lower().startswith("select"):
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error="MySQL tool only supports SELECT queries",
            )

        connection = None
        try:
            connection = pymysql.connect(
                host=tool.config.get("host", "127.0.0.1"),
                port=int(tool.config.get("port", "3306")),
                user=tool.config.get("user", ""),
                password=tool.config.get("password", ""),
                database=tool.config.get("database", ""),
                charset=tool.config.get("charset", "utf8mb4"),
            )
            with connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchmany(int(tool.config.get("max_rows", "50")))
                columns = [item[0] for item in cursor.description or []]
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error=str(exc),
            )
        finally:
            if connection is not None:
                connection.close()

        result = [dict(zip(columns, row, strict=False)) for row in rows]
        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=tool_call.arguments,
            success=True,
            result=json.dumps(result, ensure_ascii=False),
        )

    def _execute_smtp_email(self, tool: AgentTool, tool_call: ToolCall) -> ToolExecutionResult:
        missing = [key for key in ("to", "subject", "body") if not str(tool_call.arguments.get(key, "")).strip()]
        if missing:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error=f"Email tool missing required fields: {', '.join(missing)}",
            )
        smtp_host = tool.config.get("smtp_host", "")
        if not smtp_host:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error="Email tool smtp_host is empty",
            )
        sender = tool.config.get("from") or tool.config.get("username", "")
        if not sender:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error="Email tool sender is empty",
            )

        message = EmailMessage()
        message["From"] = sender
        message["To"] = str(tool_call.arguments["to"]).strip()
        message["Subject"] = str(tool_call.arguments["subject"]).strip()
        message.set_content(str(tool_call.arguments["body"]))

        try:
            with smtplib.SMTP(
                smtp_host,
                int(tool.config.get("smtp_port", "587")),
                timeout=int(tool.config.get("timeout_seconds", "30")),
            ) as smtp:
                if tool.config.get("use_tls", "true").lower() == "true":
                    smtp.starttls()
                username = tool.config.get("username", "")
                password = tool.config.get("password", "")
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        except Exception as exc:
            return ToolExecutionResult(
                tool_name=tool.name,
                arguments=tool_call.arguments,
                success=False,
                error=str(exc),
            )

        return ToolExecutionResult(
            tool_name=tool.name,
            arguments=tool_call.arguments,
            success=True,
            result=f"Email sent to {message['To']}",
        )
