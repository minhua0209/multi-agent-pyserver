from __future__ import annotations

import json

from app.core.model_client import (
    MODEL_NAME,
    RESPONSES_API_KEY,
    RESPONSES_API_URL,
    _loads_json,
    model_agent_payload,
)
from app.core.models import Agent, RoundPlan, Task
from app.planners.base import round_plan_from_dict


class CrewAITaskPlanner:
    def plan_next_round(self, task: Task, agents: list[Agent]) -> RoundPlan | None:
        try:
            from crewai import Agent as CrewAgent
            from crewai import Crew, LLM, Process
            from crewai import Task as CrewTask
        except ImportError as exc:
            raise RuntimeError("TASK_PLANNER_TYPE=crewai requires installing crewai") from exc

        planner = CrewAgent(
            role="Multi-round task dispatcher",
            goal="Plan the next executable round for a multi-agent task hub",
            backstory=(
                "You coordinate human and agent subtasks. You do not execute subtasks. "
                "You only decide the next round based on current context, dependencies, "
                "available agents, and whether human confirmation is required. "
                "All user-facing JSON text values must be written in Chinese."
            ),
            llm=self._build_llm(LLM),
            verbose=False,
        )
        reviewer = CrewAgent(
            role="Plan validator",
            goal="Ensure the dispatcher plan preserves dependencies, human gates, and valid agent ids",
            backstory=(
                "You check that parallel subtasks are independent, sequential subtasks depend on prior context, "
                "human tasks remain assignee_type=human, and every agent subtask uses an available assigned_agent_id. "
                "You also rewrite reason, final_output, title, and description fields into Chinese when needed."
            ),
            llm=self._build_llm(LLM),
            verbose=False,
        )
        crew_task = CrewTask(
            description=self._build_task_description(task, agents),
            expected_output=(
                "Only valid JSON with this schema: "
                '{"should_continue": true|false, "execution_mode": "parallel|sequential", '
                '"reason": "...", "final_output": "...", '
                '"subtasks": [{"title": "...", "description": "...", '
                '"assignee_type": "agent|human", "assigned_agent_id": "agent_id or null", '
                '"assignee_user_id": "human assignee id or empty", '
                '"assignee_user_name": "human assignee name or empty", '
                '"assignee_role": "human role or empty"}]}. '
                "The values of reason, final_output, subtasks.title, and subtasks.description must be Chinese."
            ),
            agent=planner,
        )
        validation_task = CrewTask(
            description=(
                "Review the dispatcher output and return the final corrected JSON only. "
                "Do not add Markdown or explanatory text. "
                "If any user-facing text value is English, translate or rewrite it into Chinese."
            ),
            expected_output="A corrected RoundPlan JSON object only.",
            agent=reviewer,
            context=[crew_task],
        )
        try:
            result = Crew(
                agents=[planner, reviewer],
                tasks=[crew_task, validation_task],
                process=Process.sequential,
                verbose=False,
            ).kickoff()
            return round_plan_from_dict(_loads_json(str(result)), agents)
        except Exception:
            return None

    @staticmethod
    def _build_task_description(task: Task, agents: list[Agent]) -> str:
        return json.dumps(
            {
                "instructions": [
                    "Plan only the next round, not the full workflow.",
                    "Use parallel execution only for independent subtasks.",
                    "Use sequential execution when the next subtask depends on current context.",
                    "Preserve human nodes by returning assignee_type=human.",
                    "Treat the confirmed task contract as the authoritative execution basis.",
                    "For human subtasks, infer assignee_user_id and assignee_user_name from the task text when a reviewer name is mentioned.",
                    "Return should_continue=false when no remaining subtasks exist.",
                    "Write all user-facing text fields in Chinese: reason, final_output, subtasks.title, subtasks.description.",
                    "Do not output long English explanations even if prior context is English.",
                ],
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
                "available_agents": [model_agent_payload(agent) for agent in agents],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _build_llm(llm_class):
        base_url = RESPONSES_API_URL.replace("/responses", "")
        return llm_class(model=f"openai/{MODEL_NAME}", base_url=base_url, api_key=RESPONSES_API_KEY)
