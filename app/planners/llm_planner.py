from __future__ import annotations

import json

from app.core.model_client import _loads_json, default_client, model_agent_payload
from app.core.models import Agent, RoundPlan, Task
from app.planners.base import round_plan_from_dict


class LLMTaskPlanner:
    def plan_next_round(self, task: Task, agents: list[Agent]) -> RoundPlan | None:
        system_prompt = (
            "你是多轮任务分发 agent。你需要读取主任务当前上下文，判断下一轮是否还有待执行子任务。"
            "如果前置结果不足，先创建获取前置信息的子任务；如果已有足够上下文，再创建后续子任务。"
            "每轮可以返回多个可并发执行的子任务，也可以返回一个需要同步执行的子任务。"
            "当没有待执行子任务时，should_continue=false，并给出 final_output。"
            "所有面向用户或存储展示的文本必须使用中文，包括 reason、final_output、subtasks.title、subtasks.description。"
            "如果输入上下文或 agent 描述中含有英文，也要用中文概括，不要原样输出英文长句。"
            "只返回 JSON，不要返回 Markdown。"
            '格式: {"should_continue": true|false, "execution_mode": "parallel|sequential", '
            '"reason": "...", "final_output": "...", '
            '"subtasks": [{"title": "...", "description": "...", '
            '"assignee_type": "agent|human", "assigned_agent_id": "agent_id 或 null"}]}'
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
                    "contract": task.contract.model_dump(mode="json") if task.contract else None,
                },
                "context": task.context.model_dump(mode="json"),
                "available_agents": [self._agent_payload(agent) for agent in agents],
            },
            ensure_ascii=False,
        )
        try:
            return round_plan_from_dict(_loads_json(default_client.create(system_prompt, user_prompt)), agents)
        except Exception:
            return None

    @staticmethod
    def _agent_payload(agent: Agent) -> dict:
        return model_agent_payload(agent)
