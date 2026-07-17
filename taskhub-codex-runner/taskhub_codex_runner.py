#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
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
    ui: bool
    ui_host: str
    ui_port: int


RUNNER_DIR = Path(__file__).resolve().parent


def runtime_paths() -> dict[str, Path]:
    runtime_dir = RUNNER_DIR / "runtime"
    return {
        "runtime_dir": runtime_dir,
        "pid_file": runtime_dir / "runner.pid",
        "log_file": runtime_dir / "runner.log",
        "runner_runtime_file": runtime_dir / "runner_runtime.json",
    }


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
        ui=bool_value(os.getenv("TASKHUB_UI", file_config.get("ui", False))),
        ui_host=os.getenv("TASKHUB_UI_HOST", file_config.get("ui_host", "127.0.0.1")),
        ui_port=int(os.getenv("TASKHUB_UI_PORT", file_config.get("ui_port", 8787))),
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

    def get_task(self, task_id: str) -> dict[str, Any]:
        encoded_id = parse.quote(task_id, safe="")
        return self._request("GET", f"/api/v1/tasks/{encoded_id}")

    def create_task_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/tasks/requests", payload)

    def confirm_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded_id = parse.quote(task_id, safe="")
        return self._request("POST", f"/api/v1/tasks/{encoded_id}/confirm", payload)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        encoded_id = parse.quote(task_id, safe="")
        return self._request("DELETE", f"/api/v1/tasks/{encoded_id}")

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


class RunnerState:
    def __init__(self) -> None:
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_poll_at = ""
        self.last_error = ""
        self.pending_manual: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def set_last_poll(self) -> None:
        with self.lock:
            self.last_poll_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def set_error(self, message: str) -> None:
        with self.lock:
            self.last_error = message
            self._append_event_locked("error", message)

    def add_event(self, event_type: str, message: str) -> None:
        with self.lock:
            self._append_event_locked(event_type, message)

    def add_pending_manual(self, subtask_id: str, subtask: dict[str, Any], codex_result: dict[str, Any]) -> None:
        with self.lock:
            self.pending_manual[subtask_id] = {
                "subtask": subtask,
                "codex_result": codex_result,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._append_event_locked("manual_pending", f"{subtask_id} waiting for local console handling")

    def get_pending_manual(self, subtask_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = self.pending_manual.get(subtask_id)
            return dict(item) if item is not None else None

    def remove_pending_manual(self, subtask_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.pending_manual.pop(subtask_id, None)

    def pop_pending_manual(self, subtask_id: str) -> dict[str, Any] | None:
        return self.remove_pending_manual(subtask_id)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "started_at": self.started_at,
                "last_poll_at": self.last_poll_at,
                "last_error": self.last_error,
                "pending_manual_count": len(self.pending_manual),
                "pending_manual": list(self.pending_manual.values()),
                "events": list(self.events[-100:]),
            }

    def _append_event_locked(self, event_type: str, message: str) -> None:
        self.events.append(
            {
                "type": event_type,
                "message": message,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )


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
        self.state = RunnerState()

    def run_forever(self) -> None:
        write_runner_runtime_config(RUNNER_DIR, build_runner_runtime_config(self.config))
        if self.config.auto_install_skill:
            result = ensure_skill_installed(
                self.config.codex_skill_name,
                self.config.auto_update_skill,
                runtime_config=build_skill_runtime_config(self.config),
            )
            self.log(result)
        self.log(f"runner started, user_id={self.config.user_id}, server={self.config.server_url}")
        if self.config.ui:
            self.start_web_console()
        while True:
            handled = self.poll_once()
            if self.config.once:
                return
            if not handled:
                time.sleep(self.config.poll_interval_seconds)

    def poll_once(self) -> bool:
        self.state.set_last_poll()
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
                if self.config.ui:
                    self.state.add_pending_manual(subtask_id, subtask, result)
                    self.log(f"manual handling queued in web console for {subtask_id}")
                    return True
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
            self.state.set_error(str(exc))
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

    def submit_subtask_result(
        self,
        subtask_id: str,
        result: dict[str, Any],
        raw_output: str,
        force_submit: bool = False,
    ) -> None:
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
        if not self.config.auto_submit and not force_submit:
            self.log("auto_submit=false, result payload:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        self.taskhub.submit_result(subtask_id, payload)
        self.state.add_event("submitted", f"{subtask_id} submitted with decision={decision}")
        self.log(f"submitted result for {subtask_id}: decision={decision}")

    def log(self, message: str, stream=sys.stdout) -> None:
        print(f"[taskhub-codex-runner] {message}", file=stream, flush=True)

    def start_web_console(self) -> None:
        server = create_web_console_server(self, self.config.ui_host, self.config.ui_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://{self.config.ui_host}:{self.config.ui_port}"
        self.log(f"web console started: {url}")
        if os.getenv("TASKHUB_UI_OPEN_BROWSER", "true").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                webbrowser.open(url)
            except Exception as exc:
                self.log(f"failed to open browser: {exc}", stream=sys.stderr)


def create_web_console_server(runner: TaskHubCodexRunner, host: str, port: int) -> ThreadingHTTPServer:
    class WebConsoleHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self.respond_html(build_console_html())
                return
            if self.path == "/api/status":
                self.respond_json(
                    {
                        "server_url": runner.config.server_url,
                        "user_id": runner.config.user_id,
                        "runner_id": runner.config.runner_id,
                        **runner.state.snapshot(),
                    }
                )
                return
            self.respond_json({"detail": "Not Found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path == "/api/tasks/requests":
                payload = self.read_json_body()
                try:
                    self.respond_json(runner.taskhub.create_task_request(payload), HTTPStatus.CREATED)
                except Exception as exc:
                    self.respond_json({"detail": str(exc)}, HTTPStatus.BAD_GATEWAY)
                return
            if self.path == "/api/manual-results":
                payload = self.read_json_body()
                subtask_id = str(payload.get("subtask_id", "")).strip()
                decision = normalize_decision(str(payload.get("decision", "")))
                output = str(payload.get("output", "")).strip()
                if not subtask_id or decision not in {"approved", "rejected"}:
                    self.respond_json({"detail": "subtask_id and approved/rejected decision are required"}, HTTPStatus.BAD_REQUEST)
                    return
                pending = runner.state.get_pending_manual(subtask_id)
                if pending is None:
                    self.respond_json({"detail": "pending manual subtask not found"}, HTTPStatus.NOT_FOUND)
                    return
                raw_output = json.dumps(pending.get("codex_result", {}), ensure_ascii=False)
                try:
                    runner.submit_subtask_result(
                        subtask_id,
                        {
                            "action": "submit",
                            "decision": decision,
                            "output": output or ("人工确认通过" if decision == "approved" else "人工确认驳回"),
                            "handled_by": "local_human_via_web_console",
                        },
                        raw_output,
                        force_submit=True,
                    )
                except Exception as exc:
                    runner.state.set_error(f"manual result submit failed for {subtask_id}: {exc}")
                    self.respond_json({"detail": str(exc)}, HTTPStatus.BAD_GATEWAY)
                    return
                runner.state.remove_pending_manual(subtask_id)
                self.respond_json({"ok": True})
                return
            self.respond_json({"detail": "Not Found"}, HTTPStatus.NOT_FOUND)

        def read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

        def respond_json(self, payload: dict[str, Any], status_code: int | HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status_code))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            runner.log(f"web {self.address_string()} {format % args}")

    return ThreadingHTTPServer((host, port), WebConsoleHandler)


def build_console_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TaskHub Codex Runner</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f7fb; color: #172033; }
    header { padding: 22px 28px; background: linear-gradient(135deg, #2f54eb, #13c2c2); color: white; }
    h1 { margin: 0 0 8px; font-size: 24px; }
    main { padding: 24px 28px; display: grid; gap: 18px; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .card { background: white; border: 1px solid #d9e2ff; border-radius: 8px; padding: 16px; box-shadow: 0 8px 24px rgba(34, 57, 119, .08); }
    .label { color: #5f6f89; font-size: 13px; margin-bottom: 6px; }
    .value { font-weight: 700; word-break: break-all; }
    .pending { display: grid; gap: 12px; }
    .item { border: 1px solid #b8c7ff; border-radius: 8px; padding: 14px; background: #fbfdff; }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    button { border: 0; border-radius: 6px; padding: 8px 12px; cursor: pointer; color: white; background: #2f54eb; }
    button.reject { background: #d4380d; }
    textarea { width: 100%; min-height: 72px; border: 1px solid #bdc8ea; border-radius: 6px; padding: 8px; }
    pre { max-height: 280px; overflow: auto; background: #111827; color: #d7e1ff; padding: 14px; border-radius: 8px; }
  </style>
</head>
<body>
  <header>
    <h1>TaskHub Codex Runner 控制台</h1>
    <div>本地人工节点托管、Codex 自动处理和任务发布代理。</div>
  </header>
  <main>
    <section class="grid">
      <div class="card"><div class="label">TaskHub</div><div class="value" id="server"></div></div>
      <div class="card"><div class="label">托管用户</div><div class="value" id="user"></div></div>
      <div class="card"><div class="label">待人工确认</div><div class="value" id="count"></div></div>
    </section>
    <section class="card">
      <h2>待处理人工节点</h2>
      <div class="pending" id="pending"></div>
    </section>
    <section class="card">
      <h2>最近事件</h2>
      <pre id="events"></pre>
    </section>
  </main>
  <script>
    async function loadStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.getElementById('server').textContent = data.server_url;
      document.getElementById('user').textContent = data.user_id;
      document.getElementById('count').textContent = data.pending_manual_count;
      document.getElementById('events').textContent = JSON.stringify(data.events || [], null, 2);
      const pending = document.getElementById('pending');
      pending.innerHTML = '';
      if (!data.pending_manual.length) {
        pending.innerHTML = '<div class="label">暂无需要人工介入的本地待办。</div>';
        return;
      }
      for (const item of data.pending_manual) {
        const subtask = item.subtask || {};
        const codex = item.codex_result || {};
        const div = document.createElement('div');
        div.className = 'item';
        div.innerHTML = `
          <h3>${subtask.title || subtask.id}</h3>
          <div class="label">Codex 建议</div>
          <p>${codex.output || '无'}</p>
          <textarea placeholder="请输入人工处理意见"></textarea>
          <div class="actions">
            <button data-decision="approved">通过并回填</button>
            <button class="reject" data-decision="rejected">驳回并回填</button>
          </div>
        `;
        for (const button of div.querySelectorAll('button')) {
          button.onclick = async () => {
            const output = div.querySelector('textarea').value;
            await fetch('/api/manual-results', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({subtask_id: subtask.id, decision: button.dataset.decision, output})
            });
            await loadStatus();
          };
        }
        pending.appendChild(div);
      }
    }
    loadStatus();
    setInterval(loadStatus, 3000);
  </script>
</body>
</html>"""


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
        "user_id": config.user_id,
        "runner_id": config.runner_id,
        "runner_cli_path": str(RUNNER_DIR / "runner_cli.py"),
    }


def build_runner_runtime_config(config: RunnerConfig) -> dict[str, Any]:
    return {
        "server_url": config.server_url,
        "user_id": config.user_id,
        "runner_id": config.runner_id,
    }


def write_runner_runtime_config(runner_dir: Path, runtime_config: dict[str, Any]) -> Path:
    runtime_dir = runner_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / "runner_runtime.json"
    runtime_path.write_text(
        json.dumps(runtime_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_path


def install_runner_cli_command(bin_dir: Path | None = None, runner_dir: Path | None = None) -> Path:
    target_bin_dir = bin_dir or (Path.home() / ".codex" / "bin")
    source_runner_dir = (runner_dir or RUNNER_DIR).resolve()
    target_bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = target_bin_dir / "taskhub-runner-cli"
    wrapper_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(source_runner_dir.parent))}\n"
        f"exec python3 {shlex.quote(str(source_runner_dir / 'runner_cli.py'))} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper_path.chmod(0o755)
    return wrapper_path


def ensure_skill_installed(
    skill_name: str,
    auto_update: bool = False,
    runtime_config: dict[str, Any] | None = None,
) -> str:
    source = Path(__file__).resolve().parent / "skill" / skill_name
    target = Path.home() / ".codex" / "skills" / skill_name
    message = install_skill(source, target, auto_update)
    wrapper_path = install_runner_cli_command()
    if runtime_config is not None:
        runtime_path = write_skill_runtime_config(target, runtime_config)
        return f"{message}; cli command installed: {wrapper_path}; runtime config written: {runtime_path}"
    return f"{message}; cli command installed: {wrapper_path}"


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
    parser.add_argument("--ui", action="store_true", help="Start the local web console.")
    parser.add_argument("--ui-host", default=None, help="Local web console host. Defaults to 127.0.0.1.")
    parser.add_argument("--ui-port", type=int, default=None, help="Local web console port. Defaults to 8787.")
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
    if args.ui:
        config.ui = True
    if args.ui_host:
        config.ui_host = args.ui_host
    if args.ui_port:
        config.ui_port = args.ui_port
    if args.update_skill:
        config.auto_update_skill = True
    if args.install_skill:
        write_runner_runtime_config(RUNNER_DIR, build_runner_runtime_config(config))
        print(
            ensure_skill_installed(
                config.codex_skill_name,
                config.auto_update_skill,
                runtime_config=build_skill_runtime_config(config),
            )
        )
        return 0
    try:
        TaskHubCodexRunner(config).run_forever()
    except KeyboardInterrupt:
        print("[taskhub-codex-runner] runner stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
