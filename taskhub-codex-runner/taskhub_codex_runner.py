#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


@dataclass
class RunnerConfig:
    server_url: str
    user_id: str
    runner_id: str
    codex_command: list[str]
    poll_interval_seconds: int
    codex_timeout_seconds: int
    once: bool
    dry_run: bool
    auto_submit: bool
    auto_install_skill: bool
    auto_update_skill: bool
    codex_skill_name: str


def load_config(path: str | None) -> RunnerConfig:
    file_config: dict[str, Any] = {}
    if path:
        with open(path, "r", encoding="utf-8") as file:
            file_config = json.load(file)

    codex_command = os.getenv("TASKHUB_CODEX_COMMAND", file_config.get("codex_command", "codex exec"))
    if isinstance(codex_command, str):
        codex_command = shlex.split(codex_command)

    return RunnerConfig(
        server_url=os.getenv("TASKHUB_SERVER_URL", file_config.get("server_url", "http://127.0.0.1:8000")).rstrip("/"),
        user_id=os.getenv("TASKHUB_USER_ID", file_config.get("user_id", "王大锤")),
        runner_id=os.getenv("TASKHUB_RUNNER_ID", file_config.get("runner_id", "local-codex-runner")),
        codex_command=codex_command,
        poll_interval_seconds=int(os.getenv("TASKHUB_POLL_INTERVAL_SECONDS", file_config.get("poll_interval_seconds", 5))),
        codex_timeout_seconds=int(os.getenv("TASKHUB_CODEX_TIMEOUT_SECONDS", file_config.get("codex_timeout_seconds", 300))),
        once=bool_value(os.getenv("TASKHUB_RUN_ONCE", file_config.get("once", False))),
        dry_run=bool_value(os.getenv("TASKHUB_DRY_RUN", file_config.get("dry_run", False))),
        auto_submit=bool_value(os.getenv("TASKHUB_AUTO_SUBMIT", file_config.get("auto_submit", True))),
        auto_install_skill=bool_value(os.getenv("TASKHUB_AUTO_INSTALL_SKILL", file_config.get("auto_install_skill", True))),
        auto_update_skill=bool_value(os.getenv("TASKHUB_AUTO_UPDATE_SKILL", file_config.get("auto_update_skill", False))),
        codex_skill_name=os.getenv("TASKHUB_CODEX_SKILL_NAME", file_config.get("codex_skill_name", "taskhub-codex")),
    )


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class TaskHubClient:
    def __init__(self, server_url: str) -> None:
        self.server_url = server_url

    def poll_human_subtasks(self, user_id: str) -> list[dict[str, Any]]:
        query = parse.urlencode({"assignee_user_id": user_id})
        return self._request("GET", f"/api/v1/subtasks/human?{query}")

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/tasks")

    def submit_result(self, subtask_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded_id = parse.quote(subtask_id, safe="")
        return self._request("POST", f"/api/v1/subtasks/{encoded_id}/result", payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.server_url}{path}", data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TaskHub API {method} {path} failed: {exc.code} {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"TaskHub API {method} {path} failed: {exc}") from exc
        return json.loads(body) if body else {}


class CodexClient:
    def __init__(self, command: list[str], timeout_seconds: int) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str) -> str:
        completed = subprocess.run(
            [*self.command, prompt],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or "Codex command failed"
            raise RuntimeError(stderr)
        return completed.stdout.strip()


class TaskHubCodexRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.taskhub = TaskHubClient(config.server_url)
        self.codex = CodexClient(config.codex_command, config.codex_timeout_seconds)
        self.local_claimed: set[str] = set()

    def run_forever(self) -> None:
        if self.config.auto_install_skill:
            result = ensure_skill_installed(
                self.config.codex_skill_name,
                self.config.auto_update_skill,
                runtime_config=build_skill_runtime_config(self.config),
            )
            self.log(result)
        self.log(f"runner started, user_id={self.config.user_id}, server={self.config.server_url}")
        while True:
            handled = self.poll_once()
            if self.config.once:
                return
            if not handled:
                time.sleep(self.config.poll_interval_seconds)

    def poll_once(self) -> bool:
        subtasks = self.taskhub.poll_human_subtasks(self.config.user_id)
        candidates = [item for item in subtasks if item.get("id") not in self.local_claimed]
        if not candidates:
            self.log("no delegated human subtasks")
            return False

        subtask = candidates[0]
        subtask_id = str(subtask["id"])
        self.local_claimed.add(subtask_id)
        self.log(f"claimed local human subtask: {subtask_id} {subtask.get('title', '')}")
        try:
            context = self.find_task_context(subtask_id)
            prompt = build_codex_prompt(subtask, context, self.config.runner_id)
            if self.config.dry_run:
                self.log("dry-run prompt:")
                print(prompt)
                return True

            raw_output = self.codex.run(prompt)
            result = parse_codex_result(raw_output)
            result.setdefault("output", raw_output)
            result.setdefault("decision", "need_more_info")
            if not should_auto_submit(result):
                manual_result = prompt_for_manual_result(subtask, result)
                if manual_result is None:
                    self.local_claimed.discard(subtask_id)
                    self.log(f"manual handling skipped for {subtask_id}")
                    return False
                result = manual_result
            self.submit_subtask_result(subtask_id, result, raw_output)
            return True
        except Exception as exc:
            self.local_claimed.discard(subtask_id)
            self.log(f"failed to handle {subtask_id}: {exc}", stream=sys.stderr)
            return False

    def find_task_context(self, subtask_id: str) -> dict[str, Any]:
        for task in self.taskhub.list_tasks():
            for round_item in task.get("context", {}).get("rounds", []):
                for subtask in round_item.get("subtasks", []):
                    if subtask.get("id") == subtask_id:
                        return {
                            "task_id": task.get("id"),
                            "task_title": task.get("title"),
                            "task_content": task.get("content"),
                            "task_description": task.get("description"),
                            "context_summary": task.get("context", {}).get("summary", ""),
                            "round_index": round_item.get("round_index"),
                            "round_context_before": round_item.get("context_before", ""),
                        }
        return {}

    def submit_subtask_result(self, subtask_id: str, result: dict[str, Any], raw_output: str) -> None:
        decision = normalize_decision(str(result.get("decision", "need_more_info")))
        output = str(result.get("output") or "").strip() or raw_output
        handled_by = str(result.get("handled_by") or "local_codex")
        payload = {
            "result_status": "succeeded",
            "output": output,
            "should_complete": True,
            "metadata": {
                "decision": decision,
                "handled_by": handled_by,
                "runner_id": self.config.runner_id,
                "raw_codex_output": raw_output[:4000],
            },
            "execution_mode": "async",
        }
        if not self.config.auto_submit:
            self.log("auto_submit=false, result payload:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        self.taskhub.submit_result(subtask_id, payload)
        self.log(f"submitted result for {subtask_id}: decision={decision}")

    def log(self, message: str, stream=sys.stdout) -> None:
        print(f"[taskhub-codex-runner] {message}", file=stream, flush=True)


def build_codex_prompt(subtask: dict[str, Any], context: dict[str, Any], runner_id: str) -> str:
    return f"""你是 TaskHub 本地人工节点托管助手，运行在用户本机，runner_id={runner_id}。

你的任务是辅助处理一个 TaskHub 人工待办。请阅读任务、上游上下文和人工节点要求，给出人工处理结果。

约束：
- 你不是普通业务处理 Agent，而是在代理/辅助人工节点提交处理意见。
- 如果可以自动提交，action 输出 submit。
- 如果需要本地人工确认，action 输出 needs_human，并在 questions 中列出问题。
- 如果执行失败，action 输出 failed，并在 output 中说明原因。
- 如果应该通过，decision 输出 approved。
- 如果应该驳回，decision 输出 rejected，并在 output 中说明返工原因。
- 如果信息不足，decision 输出 need_more_info，并说明缺少什么信息。
- 最终只输出 JSON，不要输出 Markdown。

输出格式：
{{"action": "submit|needs_human|failed", "decision": "approved|rejected|need_more_info", "output": "人工处理意见", "questions": []}}

人工待办：
{json.dumps(subtask, ensure_ascii=False, indent=2)}

任务上下文：
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def parse_codex_result(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decision = infer_decision(text)
        return {"action": "submit" if decision in {"approved", "rejected"} else "needs_human", "decision": decision, "output": text}
    if not isinstance(parsed, dict):
        return {"action": "needs_human", "decision": "need_more_info", "output": text}
    return parsed


def should_auto_submit(result: dict[str, Any]) -> bool:
    action = str(result.get("action") or "submit").strip().lower()
    decision = normalize_decision(str(result.get("decision", "need_more_info")))
    return action == "submit" and decision in {"approved", "rejected"}


def prompt_for_manual_result(subtask: dict[str, Any], codex_result: dict[str, Any]) -> dict[str, Any] | None:
    print("\nCodex 无法自动提交，需要人工在当前终端处理：")
    print(f"任务：{subtask.get('title') or subtask.get('id')}")
    output = str(codex_result.get("output") or "").strip()
    if output:
        print(f"原因：{output}")
    questions = codex_result.get("questions") or []
    if isinstance(questions, list) and questions:
        print("需要确认：")
        for index, question in enumerate(questions, start=1):
            print(f"{index}. {question}")
    print("\n请输入：")
    print("1 = 通过")
    print("2 = 驳回")
    print("3 = 暂不处理")
    choice = input("> ").strip()
    if choice not in {"1", "2"}:
        return None
    comment = input("请输入处理意见：").strip()
    return manual_result_from_choice(choice, comment)


def manual_result_from_choice(choice: str, comment: str) -> dict[str, Any] | None:
    normalized = choice.strip()
    if normalized == "1":
        decision = "approved"
        output = comment.strip() or "人工确认通过"
    elif normalized == "2":
        decision = "rejected"
        output = comment.strip() or "人工确认驳回"
    else:
        return None
    return {
        "action": "submit",
        "decision": decision,
        "output": output,
        "handled_by": "local_human_via_runner",
    }


def infer_decision(text: str) -> str:
    normalized = text.lower()
    if any(keyword in normalized for keyword in ["approved", "通过", "同意", "可以继续"]):
        return "approved"
    if any(keyword in normalized for keyword in ["rejected", "驳回", "不同意", "不通过"]):
        return "rejected"
    return "need_more_info"


def normalize_decision(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"approved", "approve", "pass", "通过", "同意"}:
        return "approved"
    if normalized in {"rejected", "reject", "failed", "驳回", "拒绝", "不通过"}:
        return "rejected"
    return "need_more_info"


def build_skill_runtime_config(config: RunnerConfig) -> dict[str, Any]:
    return {
        "server_url": config.server_url,
        "user_id": config.user_id,
        "runner_id": config.runner_id,
    }


def ensure_skill_installed(
    skill_name: str,
    auto_update: bool = False,
    runtime_config: dict[str, Any] | None = None,
) -> str:
    source = Path(__file__).resolve().parent / "skill" / skill_name
    target = Path.home() / ".codex" / "skills" / skill_name
    message = install_skill(source, target, auto_update)
    if runtime_config is not None:
        runtime_path = write_skill_runtime_config(target, runtime_config)
        return f"{message}; runtime config written: {runtime_path}"
    return message


def install_skill(source: Path, target: Path, auto_update: bool = False) -> str:
    source_skill = source / "SKILL.md"
    target_skill = target / "SKILL.md"
    if not source_skill.exists():
        raise RuntimeError(f"Skill source not found: {source_skill}")
    if target_skill.exists() and not auto_update:
        return f"skill already installed: {target_skill}"
    if target.exists() and auto_update:
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return f"skill installed: {target_skill}"


def write_skill_runtime_config(skill_dir: Path, runtime_config: dict[str, Any]) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = skill_dir / "taskhub_runtime.json"
    runtime_path.write_text(
        json.dumps(runtime_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TaskHub local Codex runner for delegated human subtasks.")
    parser.add_argument("--config", help="Path to config JSON file.")
    parser.add_argument("--server-url", help="TaskHub base URL supplied by runner startup, for example http://192.168.170.18:8000.")
    parser.add_argument("--user-id", help="Human assignee user id to poll delegated human subtasks for.")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the Codex prompt without submitting a result.")
    parser.add_argument("--install-skill", action="store_true", help="Install the bundled Codex skill and exit.")
    parser.add_argument("--update-skill", action="store_true", help="Overwrite the installed Codex skill when installing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.server_url:
        config.server_url = args.server_url.rstrip("/")
        os.environ["TASKHUB_SERVER_URL"] = config.server_url
    if args.user_id:
        config.user_id = args.user_id
        os.environ["TASKHUB_USER_ID"] = config.user_id
    if args.once:
        config.once = True
    if args.dry_run:
        config.dry_run = True
    if args.update_skill:
        config.auto_update_skill = True
    if args.install_skill:
        print(
            ensure_skill_installed(
                config.codex_skill_name,
                config.auto_update_skill,
                runtime_config=build_skill_runtime_config(config),
            )
        )
        return 0
    TaskHubCodexRunner(config).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
