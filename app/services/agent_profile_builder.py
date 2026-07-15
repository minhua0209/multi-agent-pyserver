from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import AgentCreate, MissingTool, SimpleAgentCreate
from app.services.tool_catalog import ToolCatalog, ToolSpec


@dataclass
class AgentProfileBuildResult:
    status: str
    message: str
    agent_create: AgentCreate | None = None
    matched_tools: list[str] = field(default_factory=list)
    missing_tools: list[MissingTool] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)


class AgentProfileBuilder:
    def __init__(self, tool_catalog: ToolCatalog | None = None) -> None:
        self.tool_catalog = tool_catalog or ToolCatalog()

    def build(self, payload: SimpleAgentCreate) -> AgentProfileBuildResult:
        ability = payload.ability.strip()
        matched_tools = self.tool_catalog.match(ability)
        missing_tool = self._detect_missing_tool(ability)
        if missing_tool is not None and not matched_tools:
            return AgentProfileBuildResult(
                status="tool_missing",
                message="当前诉求需要系统尚未接入的工具能力，请先补充工具或调整诉求。",
                missing_tools=[missing_tool],
                guidance=["可以先寻找或注册对应工具，再创建处理 agent。"],
            )

        if self._has_too_many_abilities(ability, matched_tools):
            return AgentProfileBuildResult(
                status="needs_split",
                message="当前诉求包含多个独立能力，建议分开创建多个 agent，或先补充对应工具后再创建。",
                matched_tools=[tool.type for tool in matched_tools],
                guidance=[
                    "一个处理 agent 建议只承接一类稳定能力。",
                    "例如将数据库查询、邮件发送、HTTP 调用分别创建为不同 agent。",
                ],
            )

        if matched_tools:
            return self._build_tool_agent(payload, matched_tools[0])

        return self._build_general_agent(payload)

    def _build_tool_agent(self, payload: SimpleAgentCreate, tool: ToolSpec) -> AgentProfileBuildResult:
        name = payload.name.strip() or self._default_name(tool)
        capabilities = list(dict.fromkeys(tool.capabilities))
        agent = AgentCreate(
            name=name,
            description=f"根据用户诉求自动生成：{payload.ability.strip()}",
            agent_type="processing",
            capabilities=capabilities,
            execution_config={
                "system_prompt": self._system_prompt(tool, payload.ability),
                "timeout_seconds": 60,
                "max_retries": 1,
                "max_tool_calls": 5,
            },
            tools=[tool.to_agent_tool()],
        )
        return AgentProfileBuildResult(
            status="ready",
            message="已根据诉求生成 agent 参数。",
            agent_create=agent,
            matched_tools=[tool.type],
        )

    @staticmethod
    def _build_general_agent(payload: SimpleAgentCreate) -> AgentProfileBuildResult:
        name = payload.name.strip() or "通用处理助手"
        agent = AgentCreate(
            name=name,
            description=f"根据用户诉求自动生成：{payload.ability.strip()}",
            agent_type="processing",
            capabilities=["general_processing"],
            execution_config={
                "system_prompt": (
                    "你是一个通用处理 agent。请根据任务描述完成分析、整理和输出；"
                    "如果发现需要外部工具但当前 agent 未绑定工具，请在结果中说明缺失的工具能力。"
                ),
                "timeout_seconds": 60,
                "max_retries": 1,
                "max_tool_calls": 0,
            },
        )
        return AgentProfileBuildResult(
            status="ready",
            message="未匹配到专用工具，已生成通用处理 agent。",
            agent_create=agent,
            guidance=["如需调用外部系统，请补充工具能力后再创建专用 agent。"],
        )

    @staticmethod
    def _has_too_many_abilities(ability: str, matched_tools: list[ToolSpec]) -> bool:
        if len({tool.type for tool in matched_tools}) > 1:
            return True
        action_markers = ["并且", "同时", "还要", "还能", "又能", "以及"]
        return sum(1 for marker in action_markers if marker in ability) >= 2

    @staticmethod
    def _detect_missing_tool(ability: str) -> MissingTool | None:
        lowered = ability.lower()
        if any(keyword in lowered for keyword in ["企业微信", "企微", "wechat", "微信群"]):
            return MissingTool(
                type="wechat_group_sender",
                reason="当前系统没有企业微信或微信群消息发送工具。",
                suggested_action="可以接入企业微信 webhook 工具，或用 HTTP 工具配置 webhook 地址。",
            )
        if any(keyword in lowered for keyword in ["浏览器", "网页自动化", "登录网站", "爬取"]):
            return MissingTool(
                type="browser_automation",
                reason="当前系统没有浏览器自动化工具。",
                suggested_action="可以后续接入 Playwright 类工具。",
            )
        return None

    @staticmethod
    def _default_name(tool: ToolSpec) -> str:
        names = {
            "file_write": "文件写入助手",
            "smtp_email": "邮件发送助手",
            "mysql": "数据库查询助手",
            "http": "接口调用助手",
        }
        return names.get(tool.type, "能力处理助手")

    @staticmethod
    def _system_prompt(tool: ToolSpec, ability: str) -> str:
        return (
            f"你是一个{tool.description}的处理 agent。"
            f"用户创建你的诉求是：{ability.strip()}。"
            "你需要根据任务上下文判断是否调用绑定工具，并输出清晰的执行结果。"
        )
