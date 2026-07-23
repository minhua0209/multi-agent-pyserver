from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from typing import Any
from urllib import error, request

from app.core.enums import ArtifactValidationStatus, CriterionResultStatus, TaskStatus
from app.core.models import (
    Agent,
    AgentTool,
    Artifact,
    CriterionResult,
    DeliverableResult,
    MAX_AGENT_MODEL_RETRIES,
    RoundPlan,
    SubTask,
    Task,
    TaskDraft,
    ToolCall,
    ToolExecutionResult,
    new_id,
)


DEFAULT_RESPONSES_API_URL = "http://127.0.0.1:8001/v1/responses"
DEFAULT_MODEL_NAME = "qwen3.6-35b"
RESPONSES_API_URL = os.getenv("MODEL_RESPONSES_API_URL", DEFAULT_RESPONSES_API_URL)
RESPONSES_API_KEY = os.getenv("MODEL_API_KEY", "")
CHAT_COMPLETIONS_API_URL = RESPONSES_API_URL.replace("/v1/responses", "/v1/chat/completions")
MODEL_NAME = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_MAX_OUTPUT_TOKENS = 1_024_000
MAX_OUTPUT_TOKENS = int(os.getenv("MODEL_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)))


class ModelCallError(Exception):
    pass


class AgentModelExecutionError(ModelCallError):
    def __init__(self, attempts: int, last_error: Exception | str) -> None:
        self.attempts = attempts
        error_text = str(last_error) or type(last_error).__name__
        self.last_error = _sanitize_error_text(error_text)
        super().__init__(f"Agent model execution failed after {attempts} attempts: {self.last_error}")


class OpenAIResponsesClient:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def create(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self._model(),
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
            self._url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ModelCallError(_format_request_error(exc)) from exc

        choices = body.get("choices")
        if (
            isinstance(choices, list)
            and any(
                isinstance(choice, dict) and choice.get("finish_reason") == "length"
                for choice in choices
            )
        ):
            raise ModelCallError("Chat completion response was truncated (finish_reason=length)")

        text = self.extract_text(body)
        if not text:
            raise ModelCallError("Responses API returned empty text")
        return text

    def _url(self) -> str:
        if self.url is not None:
            return self.url
        responses_api_url = os.getenv("MODEL_RESPONSES_API_URL", DEFAULT_RESPONSES_API_URL)
        if not responses_api_url:
            raise ModelCallError("MODEL_RESPONSES_API_URL is not configured")
        return responses_api_url.replace("/v1/responses", "/v1/chat/completions")

    def _api_key(self) -> str:
        if self.api_key is not None:
            return self.api_key
        return os.getenv("MODEL_API_KEY", "")

    def _model(self) -> str:
        if self.model is not None:
            return self.model
        return os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)

    def extract_text(self, response_body: dict[str, Any]) -> str:
        output_text = response_body.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()

        for output in response_body.get("output", []):
            if not isinstance(output, dict):
                continue
            text = _extract_content_text(output.get("content"))
            if text:
                return text

        for choice in response_body.get("choices", []):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            text = _extract_content_text(message.get("content"))
            if text:
                return text
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
        "每个任务只生成1到4条统一验收标准，写入 success_criteria。"
        "验收标准必须能由建议处理 agent 的能力、工具输出或人工确认结果证明；"
        "如果当前没有文件写入、代码提交、邮件发送等能力，不得生成对应要求。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"tasks": [{"draft_key": "stable_key", "title": "...", "description": "...", '
        '"confidence": 0.0, "depends_on": ["other_draft_key"], '
        '"goal": "...", "deliverable_goal": "...", '
        '"success_criteria": ["..."], '
        '"suggested_assignee_type": "agent|human", "suggested_agent_id": "agent_id 或 null"}]}'
    )
    agents_payload = [
        {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
            "tools": [
                {
                    "name": tool.name,
                    "type": tool.type,
                    "description": tool.description,
                }
                for tool in agent.tools
            ],
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
                    goal=_optional_string(item.get("goal")) or "",
                    deliverable_goal=_optional_string(item.get("deliverable_goal")) or "",
                    deliverable_kind="text",
                    deliverable_format=None,
                    deliverable_filename="",
                    deliverable_requirements=[],
                    success_criteria=_unique_strings(
                        [
                            *_string_list(item.get("deliverable_requirements")),
                            *_string_list(item.get("success_criteria")),
                        ]
                    )[:4],
                    requires_human_acceptance=False,
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


def model_tool_payload(tool: AgentTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "type": tool.type,
        "input_schema": _model_tool_input_schema(tool),
    }


def model_agent_payload(
    agent: Agent,
    tools: list[AgentTool] | None = None,
) -> dict[str, Any]:
    selected_tools = agent.tools if tools is None else tools
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "capabilities": deepcopy(agent.capabilities),
        "input_schema": deepcopy(agent.input_schema),
        "output_schema": deepcopy(agent.output_schema),
        "tools": [model_tool_payload(tool) for tool in selected_tools],
    }


def _model_tool_input_schema(tool: AgentTool) -> dict[str, Any]:
    if tool.input_schema:
        return deepcopy(tool.input_schema)
    if tool.type == "file_write":
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        }
    if tool.type == "smtp_email":
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        }

    template_fields = ("url",) if tool.type == "http" else ("query",) if tool.type == "mysql" else ()
    placeholder_names: list[str] = []
    for field in template_fields:
        template = str(tool.config.get(field, ""))
        for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
            if name not in placeholder_names:
                placeholder_names.append(name)
    if not placeholder_names:
        return {}
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in placeholder_names},
        "required": placeholder_names,
    }


def _agent_system_prompt(base_prompt: str, agent: Agent | None) -> str:
    custom_prompt = agent.execution_config.system_prompt.strip() if agent else ""
    if not custom_prompt:
        return base_prompt
    return f"{custom_prompt}\n\n{base_prompt}"


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
            "agent": model_agent_payload(agent),
        },
        ensure_ascii=False,
    )
    try:
        return default_client.create(_agent_system_prompt(system_prompt, agent), user_prompt)
    except Exception:
        return None


def execute_subtask_with_model(task: Task, subtask: SubTask, agent: Agent | None) -> str | None:
    result = execute_subtask_with_tools_model(task, subtask, agent, subtask.tool_results)
    if result is None:
        return None
    tool_calls, output = result
    if tool_calls:
        return ""
    return output


def execute_subtask_with_tools_model(
    task: Task,
    subtask: SubTask,
    agent: Agent | None,
    tool_results: list[ToolExecutionResult],
) -> tuple[list[ToolCall], str]:
    managed_file = _is_managed_file_delivery(task)
    execution_tools = _execution_tools(agent, managed_file)
    if managed_file:
        system_prompt = (
            "你是被分配到子任务的执行 agent。必须基于主任务当前上下文和子任务描述完成任务。"
            "当前主任务要求交付文件。若当前 agent 提供 file_write 工具，且本子任务负责写入最终文件，"
            "必须调用该工具真实写入文件，优先使用任务 contract 中的 deliverable_filename，"
            "并将完整 Markdown/TXT 正文放入 content 参数。"
            "系统会校验工具生成的文件；只有没有可用写入结果时才使用系统托管目录兜底。"
            "如果需要使用 agent 提供的 tools，返回 tool_calls 数组；系统会真实执行工具并把 tool_results 再传给你。"
            "如果 tool_results 已经足够完成任务，返回空 tool_calls 和最终 output。"
            "如果上下文里包含前置任务结果，需要显式利用这些结果。"
            '返回 JSON 格式: {"tool_calls": [{"tool_name": "...", "arguments": {}}], "output": "..."}'
        )
    else:
        system_prompt = (
            "你是被分配到子任务的执行 agent。必须基于主任务当前上下文和子任务描述完成任务。"
            "如果需要使用 agent 提供的 tools，返回 tool_calls 数组；系统会真实执行工具并把 tool_results 再传给你。"
            "如果 tool_results 已经足够完成任务，返回空 tool_calls 和最终 output。"
            "如果上下文里包含前置任务结果，需要显式利用这些结果。只返回简短可读文本，不要返回 Markdown。"
            '返回 JSON 格式: {"tool_calls": [{"tool_name": "...", "arguments": {}}], "output": "..."}'
        )
    agent_payload = (
        model_agent_payload(agent, execution_tools)
        if agent
        else None
    )
    user_prompt = json.dumps(
        {
            "main_task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
                "request_metadata": task.request_metadata,
                "contract": task.contract.model_dump(mode="json") if task.contract else None,
                "context_summary": task.context.summary,
                "context_artifacts": task.context.artifacts,
                "rounds": [round_item.model_dump(mode="json") for round_item in task.context.rounds],
            },
            "subtask": subtask.model_dump(mode="json"),
            "agent": agent_payload,
            "tool_results": [result.model_dump(mode="json") for result in tool_results],
        },
        ensure_ascii=False,
    )
    configured_retries = agent.execution_config.max_retries if agent else 0
    if not isinstance(configured_retries, int):
        configured_retries = 0
    max_retries = min(MAX_AGENT_MODEL_RETRIES, max(0, configured_retries))
    if managed_file:
        max_retries = max(1, max_retries)
    total_attempts = 1 + max_retries
    attempts_made = 0
    last_error: Exception | str = "Unknown agent model execution error"
    current_user_prompt = user_prompt
    for attempt in range(1, total_attempts + 1):
        attempts_made = attempt
        response_text = ""
        try:
            response_text = default_client.create(
                _agent_system_prompt(system_prompt, agent),
                current_user_prompt,
            )
            return _parse_subtask_execution_response_for_delivery(
                response_text,
                agent=agent,
                managed_file=managed_file,
                execution_tools=execution_tools,
            )
        except ValueError as exc:
            last_error = exc
            if attempt < total_attempts:
                current_user_prompt = _agent_protocol_repair_prompt(
                    original_user_prompt=user_prompt,
                    invalid_response=response_text,
                    parse_error=exc,
                )
        except (ModelCallError, error.HTTPError) as exc:
            last_error = exc
            current_user_prompt = user_prompt
        except Exception as exc:
            last_error = exc
            break

    raise AgentModelExecutionError(attempts=attempts_made, last_error=last_error)


def _agent_protocol_repair_prompt(
    *,
    original_user_prompt: str,
    invalid_response: str,
    parse_error: Exception,
) -> str:
    return json.dumps(
        {
            "instruction": (
                "上一次响应无法按要求解析。请重新生成完整响应，只返回一个合法 JSON 对象，"
                "不得使用 Markdown 代码围栏，不得在 JSON 前后添加解释。"
                "所有字符串中的换行、双引号和反斜杠必须按 JSON 规则转义。"
                "响应必须包含 tool_calls 数组和 output 字符串。"
            ),
            "parse_error": str(parse_error),
            "invalid_response": invalid_response,
            "original_task_input": json.loads(original_user_prompt),
        },
        ensure_ascii=False,
    )


def plan_next_round_with_model(task: Task, agents: list[Agent]) -> RoundPlan | None:
    system_prompt = (
        "你是多轮任务分发 agent。你需要读取主任务当前上下文，判断下一轮是否还有待执行子任务。"
        "如果任务中包含 draft 任务清单，draft 任务清单就是必须逐项完成的待办清单；"
        "你必须对照 draft 任务清单和已执行轮次，继续生成尚未完成的子任务，不能因为只完成了前置查询就结束。"
        "如果 draft 或原始诉求中出现必须人工确认、管理员确认、审批、先不要继续等要求，必须生成 assignee_type=human 的人工子任务；"
        "未生成并完成对应人工确认前，不能执行依赖确认结果的后续子任务，也不能 should_continue=false。"
        "如果前置结果不足，先创建获取前置信息的子任务；如果已有足够上下文，再创建后续子任务。"
        "每轮可以返回多个可并发执行的子任务，也可以返回一个需要同步执行的子任务。"
        "如果子任务需要人工处理，必须尽量根据任务上下文推断审核人，并填写 assignee_user_id、assignee_user_name、assignee_role；"
        "如果无法判断审核人，这三个字段可以留空，系统会交给 root 管理员。"
        "当没有待执行子任务时，should_continue=false，并给出 final_output。"
        "所有面向用户或存储展示的文本必须使用中文，包括 reason、final_output、subtasks.title、subtasks.description。"
        "如果输入上下文或 agent 描述中含有英文，也要用中文概括，不要原样输出英文长句。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"should_continue": true|false, "execution_mode": "parallel|sequential", '
        '"reason": "...", "final_output": "...", '
        '"subtasks": [{"title": "...", "description": "...", '
        '"assignee_type": "agent|human", "assigned_agent_id": "agent_id 或 null", '
        '"assignee_user_id": "审核人ID或空", "assignee_user_name": "审核人姓名或空", "assignee_role": "审核角色或空"}]}'
    )
    agents_payload = [model_agent_payload(agent) for agent in agents]
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "content": task.content,
                "draft": task.draft.model_dump(mode="json") if task.draft else None,
                "request_metadata": task.request_metadata,
                "loop_count": task.loop_count,
                "max_loop_count": task.max_loop_count,
                "draft": task.draft.model_dump(mode="json") if task.draft else None,
                "contract": task.contract.model_dump(mode="json") if task.contract else None,
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
                assignee_type="human" if item.get("assignee_type") == "human" else "agent",
                assigned_agent_id=_valid_agent_id(item.get("assigned_agent_id"), agents),
                assignee_user_id=_optional_string(item.get("assignee_user_id")) or "",
                assignee_user_name=_optional_string(item.get("assignee_user_name")) or "",
                assignee_role=_optional_string(item.get("assignee_role")) or "",
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
        "如果任务中包含 draft 任务清单，必须逐项检查 draft 中列出的任务是否都已经由执行轮次覆盖。"
        "如果 draft 或原始诉求中包含人工确认、管理员确认、审批、先不要继续等要求，"
        "未完成人工确认并且未完成其后的依赖任务时，complete 必须为 false。"
        "不能因为前置查询类子任务完成就关闭整个任务。"
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
                "draft": task.draft.model_dump(mode="json") if task.draft else None,
                "rounds": [round_item.model_dump(mode="json") for round_item in task.context.rounds],
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


def judge_condition_with_model(task: Task, subtask: SubTask) -> dict[str, Any] | None:
    config = subtask.result_metadata.get("config", {})
    condition_options = _condition_options(config)
    allowed_decisions = [option["value"] for option in condition_options] or _string_list(config.get("allowed_decisions")) or [
        "approved",
        "rejected",
        "need_more_info",
    ]
    condition_content = str(
        config.get("condition_content")
        or _format_condition_options(condition_options)
        or config.get("condition_description")
        or subtask.description
        or subtask.title
    ).strip()
    system_prompt = (
        "你是流程模版里的智能条件判断节点。"
        "你只能根据给定的任务上下文摘要、最近一轮子任务输出和最近一轮结构化结果做判断，"
        "不要创造输入里不存在的信息。"
        "判断目标优先来自 condition.condition_options；每个 option.value 是可返回的 decision，"
        "option.content 是该分支的自然语言判断标准。"
        "如果 condition_options 为空，再参考 condition.condition_content。"
        "decision 必须且只能从 allowed_decisions 中选择。"
        "如果证据不足、无法判断或无法匹配任何 allowed_decisions，decision 返回空字符串，"
        "reason 返回“无法正常判断条件”。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"decision": "...", "reason": "...", "matched_source": "...", "confidence": 0.0}'
    )
    user_prompt = json.dumps(
        {
            "condition": {
                "id": subtask.id,
                "title": subtask.title,
                "description": subtask.description,
                "condition_content": condition_content,
                "condition_options": condition_options,
                "allowed_decisions": allowed_decisions,
            },
            "task_context": {
                "summary": task.context.summary,
            },
            "latest_round": _latest_round_condition_payload(task),
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None

    decision = str(data.get("decision") or "").strip()
    if not decision or decision not in allowed_decisions:
        return None
    return {
        "decision": decision,
        "reason": str(data.get("reason") or "智能条件判断完成"),
        "matched_source": str(data.get("matched_source") or ""),
        "confidence": _float_or_zero(data.get("confidence")),
        "condition_content": condition_content,
        "condition_options": condition_options,
    }


def _condition_options(config: dict[str, Any]) -> list[dict[str, str]]:
    raw_options = config.get("condition_options")
    if not isinstance(raw_options, list):
        return []
    options = []
    for item in raw_options:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        content = str(item.get("content") or "").strip()
        if value:
            options.append({"value": value, "content": content})
    return options


def _format_condition_options(options: list[dict[str, str]]) -> str:
    return "\n".join(f"{item['value']}: {item.get('content', '')}" for item in options).strip()


def _latest_round_condition_payload(task: Task) -> dict[str, Any]:
    for round_item in reversed(task.context.rounds):
        completed_subtasks = [subtask for subtask in round_item.subtasks if subtask.status == TaskStatus.SUCCEEDED]
        if not completed_subtasks:
            continue
        return {
            "round_index": round_item.round_index,
            "execution_mode": round_item.execution_mode,
            "reason": round_item.reason,
            "context_before": round_item.context_before,
            "context_after": round_item.context_after,
            "subtasks": [
                {
                    "node_id": _workflow_node_id_from_subtask_id(task.id, item.id),
                    "title": item.title,
                    "description": item.description,
                    "assignee_type": item.assignee_type,
                    "output": item.output,
                    "result_metadata": item.result_metadata,
                }
                for item in completed_subtasks
            ],
        }
    return {"subtasks": []}


def _workflow_node_id_from_subtask_id(task_id: str, subtask_id: str) -> str:
    prefix = f"{task_id}_"
    return subtask_id[len(prefix) :] if subtask_id.startswith(prefix) else subtask_id


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_success_criteria_with_model(
    task: Task,
    execution_output: str,
) -> list[CriterionResult] | None:
    if task.contract is None:
        return []
    system_prompt = (
        "你是任务统一验收 agent。必须逐项判断完整执行证据是否满足给定验收标准。"
        "证据包括最终输出、上下文摘要、各轮节点输出、人工意见、工具结果和有效交付物。"
        "不能遗漏标准，也不能仅因为流程结束或有输出就判定通过。"
        "引用交付物证据时必须填写输入中存在的 artifact_id。只返回 JSON，不要返回 Markdown。"
        '格式: {"criterion_results": [{"criterion_id": "...", '
        '"status": "passed|failed|pending", "evidence_text": "...", "reason": "..."}]}'
    )
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "goal": task.contract.goal,
                "deliverable_goal": task.contract.deliverable_goal,
            },
            "acceptance_criteria": [
                criterion.model_dump(mode="json") for criterion in task.contract.success_criteria
            ],
            "execution_evidence": _completion_evidence_payload(task, execution_output),
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None
    raw_results = data.get("criterion_results")
    if not isinstance(raw_results, list):
        return None

    criteria = task.contract.success_criteria
    expected_ids = {criterion.id for criterion in criteria}
    known_result_ids = [
        str(item.get("criterion_id") or "").strip()
        for item in raw_results
        if isinstance(item, dict)
        and str(item.get("criterion_id") or "").strip() in expected_ids
    ]
    if len(known_result_ids) != len(set(known_result_ids)):
        return [
            CriterionResult(
                criterion_id=criterion.id,
                status=CriterionResultStatus.PENDING,
                reason="Model evaluation contains duplicate criterion IDs",
            )
            for criterion in criteria
        ]

    results_by_id: dict[str, CriterionResult] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        criterion_id = str(item.get("criterion_id") or "").strip()
        if criterion_id not in expected_ids:
            continue
        try:
            results_by_id[criterion_id] = CriterionResult(
                criterion_id=criterion_id,
                status=CriterionResultStatus(str(item.get("status") or "pending")),
                evidence_artifact_ids=_string_list(item.get("evidence_artifact_ids")),
                evidence_text=str(item.get("evidence_text") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
            )
        except (TypeError, ValueError):
            continue
    return [
        results_by_id.get(criterion.id)
        or CriterionResult(
            criterion_id=criterion.id,
            status=CriterionResultStatus.PENDING,
            reason="Model evaluation did not return this criterion",
        )
        for criterion in criteria
    ]


def _artifact_evaluation_payload(artifact: Artifact) -> dict[str, Any]:
    metadata_fields = (
        "tool_name",
        "tool_type",
        "managed_final_delivery",
        "deliverable_format",
        "content_length",
    )
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind.value,
        "name": artifact.name,
        "content": _bounded_text(artifact.content),
        "media_type": artifact.media_type,
        "metadata": {
            field: artifact.metadata[field]
            for field in metadata_fields
            if field in artifact.metadata
        },
    }


def _completion_evidence_payload(task: Task, execution_output: str) -> dict[str, Any]:
    active_execution_id = task.active_execution_id or ""
    subtasks = [
        (round_item.round_index, subtask)
        for round_item in task.context.rounds
        for subtask in round_item.subtasks
    ][-40:]
    valid_artifacts = [
        artifact
        for artifact in task.artifacts
        if artifact.validation_status == ArtifactValidationStatus.VALID
        and (not active_execution_id or artifact.execution_id == active_execution_id)
    ][-30:]
    return {
        "final_output": _bounded_text(execution_output),
        "context_summary": _bounded_text(task.context.summary),
        "node_outputs": [
            {
                "round_index": round_index,
                "title": subtask.title,
                "assignee_type": subtask.assignee_type,
                "status": subtask.status.value,
                "output": _bounded_text(subtask.output),
                "result_metadata": _bounded_json_value(subtask.result_metadata),
                "tool_results": [
                    {
                        "tool_name": result.tool_name,
                        "tool_type": result.tool_type,
                        "success": result.success,
                        "result": _bounded_json_value(result.result),
                        "error": _bounded_text(result.error),
                    }
                    for result in subtask.tool_results[-10:]
                ],
            }
            for round_index, subtask in subtasks
        ],
        "valid_artifacts": [
            _artifact_evaluation_payload(artifact)
            for artifact in valid_artifacts
        ],
    }


def _bounded_text(value: Any, limit: int = 6000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}...[truncated]"


def _bounded_json_value(value: Any, limit: int = 4000) -> Any:
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return _bounded_text(value, limit)
    if len(serialized) <= limit:
        return value
    return f"{serialized[:limit]}...[truncated]"


def evaluate_deliverable_requirements_with_model(
    task: Task,
    selected_artifacts: list[Artifact],
) -> list[DeliverableResult] | None:
    if task.contract is None or not task.contract.deliverable_requirements:
        return []
    requirements = task.contract.deliverable_requirements
    selected_artifact_ids = {artifact.id for artifact in selected_artifacts}
    system_prompt = (
        "你是任务交付物评估 agent。必须逐项判断选中的交付物是否满足交付要求。"
        "只能引用输入中 selected_artifacts 的 artifact_id，不能遗漏要求。"
        "只返回 JSON，不要返回 Markdown。"
        '格式: {"deliverable_results": [{"requirement_id": "...", '
        '"status": "passed|failed|pending", "artifact_ids": ["..."], "reason": "..."}]}'
    )
    user_prompt = json.dumps(
        {
            "task": {
                "id": task.id,
                "goal": task.contract.goal,
                "deliverable_goal": task.contract.deliverable_goal,
            },
            "deliverable_requirements": [
                requirement.model_dump(mode="json") for requirement in requirements
            ],
            "selected_artifacts": [
                _artifact_evaluation_payload(artifact)
                for artifact in selected_artifacts
            ],
        },
        ensure_ascii=False,
    )
    try:
        data = _loads_json(default_client.create(system_prompt, user_prompt))
    except Exception:
        return None
    raw_results = data.get("deliverable_results")
    if not isinstance(raw_results, list):
        return None

    expected_ids = {requirement.id for requirement in requirements}
    known_result_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in raw_results
        if isinstance(item, dict)
        and str(item.get("requirement_id") or "").strip() in expected_ids
    ]
    if len(known_result_ids) != len(set(known_result_ids)):
        return [
            DeliverableResult(
                requirement_id=requirement.id,
                status=CriterionResultStatus.PENDING,
                reason="Model evaluation contains duplicate requirement IDs",
            )
            for requirement in requirements
        ]

    results_by_id: dict[str, DeliverableResult] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id") or "").strip()
        if requirement_id not in expected_ids:
            continue
        artifact_ids = _string_list(item.get("artifact_ids"))
        try:
            status = CriterionResultStatus(str(item.get("status") or "pending"))
        except ValueError:
            status = CriterionResultStatus.PENDING
        reason = str(item.get("reason") or "").strip()
        if any(artifact_id not in selected_artifact_ids for artifact_id in artifact_ids):
            status = CriterionResultStatus.PENDING
            artifact_ids = []
            reason = "Deliverable result references artifacts outside the selected set"
        elif status == CriterionResultStatus.PASSED and not artifact_ids:
            status = CriterionResultStatus.PENDING
            reason = "Passed deliverable result must reference selected artifacts"
        try:
            results_by_id[requirement_id] = DeliverableResult(
                requirement_id=requirement_id,
                status=status,
                artifact_ids=artifact_ids,
                reason=reason,
            )
        except (TypeError, ValueError):
            continue
    return [
        results_by_id.get(requirement.id)
        or DeliverableResult(
            requirement_id=requirement.id,
            status=CriterionResultStatus.PENDING,
            reason="Model evaluation did not return this requirement",
        )
        for requirement in requirements
    ]


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


def _parse_subtask_execution_response(text: str) -> tuple[list[ToolCall], str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Agent model response was empty")

    cleaned = text.strip()
    json_envelope = _json_object_envelope(cleaned)
    if json_envelope is None:
        return [], cleaned
    data = json.loads(json_envelope)
    if not isinstance(data, dict):
        raise ValueError("Agent model response JSON must be an object")
    raw_tool_calls = data.get("tool_calls")
    output = data.get("output")
    if not isinstance(raw_tool_calls, list):
        raise ValueError("Agent model response field 'tool_calls' must be a list")
    if not isinstance(output, str):
        raise ValueError("Agent model response field 'output' must be a string")

    tool_calls = []
    for index, item in enumerate(raw_tool_calls):
        if not isinstance(item, dict):
            raise ValueError(f"Agent model response tool_calls[{index}] must be an object")
        tool_name = item.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"Agent model response tool_calls[{index}].tool_name must be a non-empty string")
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError(f"Agent model response tool_calls[{index}].arguments must be an object")
        tool_calls.append(ToolCall(tool_name=tool_name.strip(), arguments=arguments))
    return tool_calls, output.strip()


def _parse_subtask_execution_response_for_delivery(
    text: str,
    *,
    agent: Agent | None,
    managed_file: bool,
    execution_tools: list[AgentTool],
) -> tuple[list[ToolCall], str]:
    if managed_file and not _looks_like_tool_calls_json(text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Agent model response was empty")
        return [], text.strip()
    tool_calls, output = _parse_subtask_execution_response(text)
    if managed_file:
        _validate_managed_tool_calls(tool_calls, agent, execution_tools)
    return tool_calls, output


def _looks_like_tool_calls_json(text: str) -> bool:
    if not isinstance(text, str):
        return False
    cleaned = text.strip()
    if cleaned.startswith("{"):
        candidate = cleaned
    else:
        first_line, separator, fenced_body = cleaned.partition("\n")
        if first_line.strip().lower() != "```json" or not separator:
            return False
        closing_fence = re.search(r"(?m)^[ \t]*```[ \t]*\r?$", fenced_body)
        if closing_fence is not None:
            if fenced_body[closing_fence.end() :].strip():
                return False
            fenced_body = fenced_body[: closing_fence.start()]
        candidate = fenced_body.lstrip()
    if not candidate.startswith("{"):
        return False
    has_tool_calls, root_end = _scan_top_level_object(candidate, "tool_calls")
    if not has_tool_calls:
        return False
    return root_end is None or not candidate[root_end + 1 :].strip()


def _scan_top_level_object(candidate: str, expected_key: str) -> tuple[bool, int | None]:
    depth = 0
    quote = ""
    escaped = False
    string_start = 0
    has_expected_key = False
    for index, character in enumerate(candidate):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                closed_quote = quote
                quote = ""
                if depth != 1:
                    continue
                next_index = index + 1
                while next_index < len(candidate) and candidate[next_index].isspace():
                    next_index += 1
                if next_index >= len(candidate) or candidate[next_index] != ":":
                    continue
                if closed_quote == '"':
                    try:
                        key = json.loads(candidate[string_start : index + 1])
                    except (TypeError, ValueError):
                        continue
                else:
                    key = candidate[string_start + 1 : index]
                if key == expected_key:
                    has_expected_key = True
            continue

        if character in {'"', "'"}:
            quote = character
            string_start = index
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
            if depth <= 0:
                return has_expected_key, index
    return has_expected_key, None


def _validate_managed_tool_calls(
    tool_calls: list[ToolCall],
    agent: Agent | None,
    execution_tools: list[AgentTool],
) -> None:
    visible_tool_names = {tool.name for tool in execution_tools}
    for tool_call in tool_calls:
        tool = next(
            (candidate for candidate in agent.tools if candidate.name == tool_call.tool_name),
            None,
        ) if agent else None
        if (
            tool is None
            or tool_call.tool_name not in visible_tool_names
        ):
            raise ValueError("File delivery received a non-visible tool call")


def _json_object_envelope(text: str) -> str | None:
    if text.startswith("{"):
        return text
    if not text.startswith("```"):
        return None

    first_line, separator, fenced_body = text.partition("\n")
    if first_line.strip().lower() != "```json":
        return None
    if not separator or not fenced_body.rstrip().endswith("```"):
        raise ValueError("Agent model JSON fence must wrap a complete object")

    json_text = fenced_body.rstrip()[:-3].strip()
    if not json_text.startswith("{"):
        return None
    return json_text


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


def _is_managed_file_delivery(task: Task) -> bool:
    return bool(task.contract and task.contract.deliverable_kind == "file")


def _execution_tools(agent: Agent | None, managed_file: bool) -> list[AgentTool]:
    tools = list(agent.tools) if agent else []
    visible_tools = []
    seen_names = set()
    for tool in tools:
        if tool.name in seen_names:
            continue
        seen_names.add(tool.name)
        visible_tools.append(tool)
    return visible_tools


def _sanitize_error_text(text: str) -> str:
    credential_labels = (
        r"(?:model[ _-]?api[ _-]?key|api[ _-]?key|access[ _-]?token|"
        r"refresh[ _-]?token|client[ _-]?secret|credential|token|password|secret|cookie)"
    )
    separator_sensitive_labels = rf"(?:authorization|{credential_labels})"
    sensitive_value_pattern = re.compile(
        rf"(?i)(\b{separator_sensitive_labels}\b[\"']?"
        r"(?:\s+(?:provided(?:\s+is)?|is))?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\r\n,;]+)"
    )
    bare_sensitive_value_pattern = re.compile(
        rf"(?i)(\b{credential_labels}\b[\"']?"
        r"(?:\s+(?:provided(?:\s+is)?|is))?\s+)"
        r"(?!(?:limit|count|expired|expiration|quota|budget|usage|window|length|"
        r"maximum|max|minimum|min|invalid|missing)\b)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)"
    )
    sanitized = re.sub(r"(?i)\bsk-[a-z0-9._-]{6,}\b", "[REDACTED]", text)
    sanitized = re.sub(
        r"(?i)\b(bearer|basic)\s+(?:\"[^\"]*\"|'[^']*'|[^\s,;\"']+)",
        r"\1 [REDACTED]",
        sanitized,
    )
    sanitized = sensitive_value_pattern.sub(r"\1[REDACTED]", sanitized)
    sanitized = bare_sensitive_value_pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized[:1000]


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


def _unique_strings(values: list[str]) -> list[str]:
    unique_values = []
    seen = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        unique_values.append(value)
        seen.add(key)
    return unique_values


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
