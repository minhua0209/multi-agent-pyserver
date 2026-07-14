from __future__ import annotations

import json
from urllib import request

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
