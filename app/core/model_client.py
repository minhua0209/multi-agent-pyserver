from __future__ import annotations

import json
import os
from typing import Any
from urllib import request

from app.core.models import Agent, RoundPlan, SubTask, Task, TaskDraft, ToolCall, ToolExecutionResult, new_id


RESPONSES_API_URL = os.getenv("MODEL_RESPONSES_API_URL", "http://192.168.18.94:30377/v1/responses")
RESPONSES_API_KEY = os.getenv("MODEL_API_KEY", "")
CHAT_COMPLETIONS_API_URL = RESPONSES_API_URL.replace("/v1/responses", "/v1/chat/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.6-35b")
REQUEST_TIMEOUT_SECONDS = 60
MAX_OUTPUT_TOKENS = 512


class ModelCallError(Exception):
    pass


class OpenAIResponsesClient:
    def __init__(
        self,
        url: str = CHAT_COMPLETIONS_API_URL,
        api_key: str = RESPONSES_API_KEY,
        model: str = MODEL_NAME,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def create(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        req = request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ModelCallError(_format_request_error(exc)) from exc

        text = self.extract_text(body)
        if not text:
            raise ModelCallError("Responses API returned empty text")
        return text

    def extract_text(self, response_body: dict[str, Any]) -> str:
        output_text = response_body.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()

        for output in response_body.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    return text.strip()

        for choice in response_body.get("choices", []):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
        return ""


default_client = OpenAIResponsesClient()


def recognize_task_with_model(content: str, agents: list[Agent] | None = None) -> TaskDraft | None:
    drafts = recognize_tasks_with_model(content, agents or [])
    return drafts[0] if drafts else None


def recognize_tasks_with_model(content: str, agents: list[Agent] | None = None) -> list[TaskDraft]:
    agents = agents or []
    system_prompt = (
        "你是任务意图识别 agent。需要结合当前系统已注册的处理 agent 列表，"
        "从上游请求中提取多个互相独立、可分别执行的任务，并为每个任务建议处理方。"
        "拆分任务时优先按可被某个处理 agent 独立完成的粒度拆分；"
        "如果任务之间存在明确先后关系，必须拆成多个任务，并使用 draft_key 和 depends_on 表达依赖关系；"
        "depends_on 填前置任务的 draft_key。没有依赖则为空数组。"
        "如果没有合适 agent，处理方选择 human。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"tasks": [{"draft_key": "stable_key", "title": "...", "description": "...", '
        '"confidence": 0.0, "depends_on": ["other_draft_key"], '
        '"suggested_assignee_type": "agent|human", "suggested_agent_id": "agent_id 或 null"}]}'
    )
    agents_payload = [
        {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
        }
        for agent in agents
    ]
    user_prompt = json.dumps(
        {
            "request_content": content,
            "available_agents": agents_payload,
            "instructions": "请提取任务列表；如果只有一个任务，也放入 tasks 数组。",
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
        raw_tasks = data.get("tasks")
        if raw_tasks is None and {"title", "description"}.issubset(data):
            raw_tasks = [data]
        if not isinstance(raw_tasks, list):
            return []
        drafts = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            if not title or not description:
                continue
            drafts.append(
                TaskDraft(
                    title=title,
                    description=description,
                    confidence=float(item.get("confidence", 0.8)),
                    draft_key=_optional_string(item.get("draft_key")),
                    depends_on=_string_list(item.get("depends_on")),
                    suggested_assignee_type=_normalize_assignee_type(item.get("suggested_assignee_type")),
                    suggested_agent_id=_valid_agent_id(item.get("suggested_agent_id"), agents),
                )
            )
        return drafts
    except Exception:
        return []


def dispatch_with_model(task: Task, agents: list[Agent]) -> Agent | None:
    system_prompt = (
        "你是任务分发 agent。根据任务和 agent 能力选择最合适的 agent。"
        "如果没有合适 agent，选择 human。只返回 JSON，不要返回 Markdown。"
        '格式: {"assignee_type": "agent|human", "agent_id": "agent_id 或空字符串", "reason": "..."}'
    )
    agents_payload = [
        {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
        }
        for agent in agents
    ]
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
            },
            "agents": agents_payload,
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None

    if data.get("assignee_type") != "agent":
        return None
    agent_id = data.get("agent_id")
    return next((agent for agent in agents if agent.id == agent_id), None)


def execute_agent_with_model(task: Task, agent: Agent) -> str | None:
    system_prompt = (
        "你是被分配到任务的执行 agent。请基于任务信息给出执行结果。"
        "只返回简短可读文本，不要返回 Markdown。"
    )
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
            },
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "capabilities": agent.capabilities,
                "tools": [tool.model_dump(mode="json") for tool in agent.tools],
            },
        },
        ensure_ascii=False,
    )
    try:
        return default_client.create(system_prompt, user_prompt)
    except Exception:
        return None


def execute_subtask_with_model(task: Task, subtask: SubTask, agent: Agent | None) -> str | None:
    tool_calls, output = execute_subtask_with_tools_model(task, subtask, agent, subtask.tool_results)
    if tool_calls:
        return ""
    return output


def execute_subtask_with_tools_model(
    task: Task,
    subtask: SubTask,
    agent: Agent | None,
    tool_results: list[ToolExecutionResult],
) -> tuple[list[ToolCall], str]:
    system_prompt = (
        "你是被分配到子任务的执行 agent。必须基于主任务当前上下文和子任务描述完成任务。"
        "如果需要使用 agent 提供的 tools，返回 tool_calls 数组；系统会真实执行工具并把 tool_results 再传给你。"
        "如果 tool_results 已经足够完成任务，返回空 tool_calls 和最终 output。"
        "如果上下文里包含前置任务结果，需要显式利用这些结果。只返回简短可读文本，不要返回 Markdown。"
        '返回 JSON 格式: {"tool_calls": [{"tool_name": "...", "arguments": {}}], "output": "..."}'
    )
    user_prompt = json.dumps(
        {
            "main_task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
                "context_summary": task.context.summary,
                "rounds": [round_item.model_dump(mode="json") for round_item in task.context.rounds],
            },
            "subtask": subtask.model_dump(mode="json"),
            "agent": agent.model_dump(mode="json") if agent else None,
            "tool_results": [result.model_dump(mode="json") for result in tool_results],
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return [], ""

    tool_calls = []
    for item in data.get("tool_calls", []):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "")).strip()
        if not tool_name:
            continue
        arguments = item.get("arguments", {})
        tool_calls.append(ToolCall(tool_name=tool_name, arguments=arguments if isinstance(arguments, dict) else {}))
    return tool_calls, str(data.get("output", "")).strip()


def plan_next_round_with_model(task: Task, agents: list[Agent]) -> RoundPlan | None:
    system_prompt = (
        "你是多轮任务分发 agent。你需要读取主任务当前上下文，判断下一轮是否还有待执行子任务。"
        "如果前置结果不足，先创建获取前置信息的子任务；如果已有足够上下文，再创建后续子任务。"
        "每轮可以返回多个可并发执行的子任务，也可以返回一个需要同步执行的子任务。"
        "当没有待执行子任务时，should_continue=false，并给出 final_output。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"should_continue": true|false, "execution_mode": "parallel|sequential", '
        '"reason": "...", "final_output": "...", '
        '"subtasks": [{"title": "...", "description": "...", "assigned_agent_id": "agent_id 或 null"}]}'
    )
    agents_payload = [
        {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
            "tools": [tool.model_dump(mode="json") for tool in agent.tools],
        }
        for agent in agents
    ]
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
                "loop_count": task.loop_count,
                "max_loop_count": task.max_loop_count,
            },
            "context": task.context.model_dump(mode="json"),
            "available_agents": agents_payload,
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None

    subtasks = []
    for item in data.get("subtasks", []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        if not title or not description:
            continue
        subtasks.append(
            SubTask(
                id=new_id("subtask"),
                title=title,
                description=description,
                assigned_agent_id=_valid_agent_id(item.get("assigned_agent_id"), agents),
            )
        )
    return RoundPlan(
        should_continue=bool(data.get("should_continue", bool(subtasks))),
        execution_mode="sequential" if data.get("execution_mode") == "sequential" else "parallel",
        reason=str(data.get("reason", "")),
        final_output=str(data.get("final_output", "")),
        subtasks=subtasks,
    )


def judge_completion_with_model(task: Task, execution_output: str) -> bool | None:
    system_prompt = (
        "你是任务完成度判断 agent。判断当前执行结果是否足以关闭任务。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"complete": true|false, "reason": "..."}'
    )
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
                "loop_count": task.loop_count,
                "max_loop_count": task.max_loop_count,
            },
            "execution_output": execution_output,
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None
    complete = data.get("complete")
    return complete if isinstance(complete, bool) else None


def _loads_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Model response JSON must be an object")
    return data


def _normalize_assignee_type(value: Any) -> str:
    return "agent" if value == "agent" else "human"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _valid_agent_id(value: Any, agents: list[Agent]) -> str | None:
    if not isinstance(value, str):
        return None
    agent_ids = {agent.id for agent in agents}
    return value if value in agent_ids else None


def _format_request_error(exc: Exception) -> str:
    if hasattr(exc, "read"):
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        if body:
            return f"{exc}; body={body[:1000]}"
    return str(exc)
