#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

RUNNER_DIR = Path(__file__).resolve().parent
DEFAULT_RUNNER_RUNTIME_PATH = RUNNER_DIR / "runtime" / "runner_runtime.json"


def load_runner_runtime_config(runner_dir: Path | None = None, path: str | None = None) -> dict[str, Any]:
    runtime_path = Path(path).expanduser() if path else (runner_dir or RUNNER_DIR) / "runtime" / "runner_runtime.json"
    if not runtime_path.exists():
        raise RuntimeError(f"TaskHub runner runtime config not found: {runtime_path}")
    return json.loads(runtime_path.read_text(encoding="utf-8"))


class RunnerBrokerClient:
    def __init__(self, ipc_dir: Path, timeout_seconds: float = 35.0) -> None:
        self.ipc_dir = ipc_dir
        self.requests_dir = ipc_dir / "requests"
        self.responses_dir = ipc_dir / "responses"
        self.timeout_seconds = timeout_seconds

    def get_current_user(self) -> dict[str, Any]:
        return self._request("get_current_user", {})

    def create_task_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("create_task_request", {"payload": payload})

    def confirm_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("confirm_task", {"task_id": task_id, "payload": payload})

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self._request("cancel_task", {"task_id": task_id})

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._request("list_tasks", {})

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("get_task", {"task_id": task_id})

    def _request(self, action: str, params: dict[str, Any]) -> Any:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        request_id = f"{time.time_ns()}_{uuid.uuid4().hex}"
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        temporary_path = self.requests_dir / f".{request_id}.tmp"
        temporary_path.write_text(
            json.dumps(
                {"request_id": request_id, "action": action, "params": params},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(request_path)
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while time.monotonic() < deadline:
                if response_path.exists():
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                    if response.get("ok") is True:
                        return response.get("result")
                    raise RuntimeError(str(response.get("error") or "Runner command failed"))
                time.sleep(0.05)
        finally:
            response_path.unlink(missing_ok=True)
        request_path.unlink(missing_ok=True)
        raise RuntimeError(
            "TaskHub runner command broker did not respond. "
            "Start the runner with start_runner.sh before using the CLI."
        )


def taskhub_client(runtime_config: dict[str, Any]) -> RunnerBrokerClient:
    user_id = str(runtime_config.get("user_id") or "").strip()
    if not user_id:
        raise RuntimeError("TaskHub user_id is missing in runner runtime config")
    ipc_dir_value = str(runtime_config.get("ipc_dir") or "").strip()
    ipc_dir = Path(ipc_dir_value).expanduser() if ipc_dir_value else RUNNER_DIR / "runtime" / "ipc"
    timeout_seconds = float(os.getenv("TASKHUB_CLI_TIMEOUT_SECONDS", "35"))
    client = RunnerBrokerClient(ipc_dir, timeout_seconds=timeout_seconds)
    current_user = client.get_current_user()
    current_user_id = str(current_user.get("id") or "")
    if current_user_id != user_id:
        raise RuntimeError(
            f"TaskHub user mismatch: configured user_id={user_id}, "
            f"current user id={current_user_id or '<missing>'}"
        )
    return client


def format_task_request_response(raw: dict[str, Any]) -> dict[str, Any]:
    tasks = []
    for task in raw.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
        tasks.append(
            {
                "task_id": task.get("id", ""),
                "submitted_title": task.get("title", ""),
                "draft_title": draft.get("title") or task.get("title", ""),
                "draft_description": draft.get("description") or task.get("description") or task.get("content") or "",
                "draft_contract": draft_contract_from_task(task),
            }
        )
    return {
        "ok": True,
        "request_id": raw.get("request_id", ""),
        "tasks": tasks,
    }


def error_payload(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def load_json_file(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def unique_text_items(values: list[Any], limit: int = 10) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("description", "")
        text = clean_text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def draft_contract_from_task(
    task: dict[str, Any],
    fallback_description: str = "",
) -> dict[str, Any]:
    draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
    title = clean_text(task.get("title") or draft.get("title") or task.get("id"))
    request_text = clean_text(
        task.get("description")
        or task.get("content")
        or draft.get("description")
        or fallback_description
        or title
    )
    goal = clean_text(draft.get("goal") or request_text or title)
    deliverable_goal = clean_text(
        draft.get("deliverable_goal") or f"形成可评审的{title}交付物"
    )
    deliverable_kind = "file" if draft.get("deliverable_kind") == "file" else "text"
    deliverable_format = (
        clean_text(draft.get("deliverable_format")) or "markdown"
        if deliverable_kind == "file"
        else None
    )
    deliverable_filename = (
        clean_text(draft.get("deliverable_filename"))
        if deliverable_kind == "file"
        else ""
    )
    criteria = unique_text_items(
        [
            *(draft.get("deliverable_requirements") or []),
            *(draft.get("success_criteria") or []),
        ]
    )
    if not criteria:
        criteria = [f"交付结果满足：{request_text or goal}"]
    return {
        "goal": goal,
        "deliverable_goal": deliverable_goal,
        "deliverable_kind": deliverable_kind,
        "deliverable_format": deliverable_format,
        "deliverable_filename": deliverable_filename,
        "deliverable_requirements": [],
        "success_criteria": [
            {"id": "", "description": description}
            for description in criteria
        ],
        "requires_human_acceptance": bool(draft.get("requires_human_acceptance")),
    }


def command_publish_task(args: argparse.Namespace, client: RunnerBrokerClient) -> dict[str, Any]:
    payload_file = getattr(args, "payload_file", None)
    if payload_file:
        payload = load_json_file(payload_file)
    else:
        metadata = json.loads(args.metadata_json) if args.metadata_json else {}
        payload = {
            "source_type": args.source_type,
            "title": args.title,
            "content": args.content,
            "metadata": metadata,
        }
        task_type = clean_text(getattr(args, "task_type", ""))
        attachment_ids = list(getattr(args, "attachment_id", []) or [])
        workflow_id = clean_text(getattr(args, "workflow_id", ""))
        if workflow_id:
            metadata.update(
                {
                    "execution_mode": "workflow_template",
                    "workflow_id": workflow_id,
                }
            )
            task_type = "manual_orchestration"
        if task_type:
            payload["task_type"] = task_type
        if attachment_ids:
            payload["attachment_ids"] = attachment_ids
            metadata["attachment_ids"] = attachment_ids
    return format_task_request_response(client.create_task_request(payload))


def command_confirm_task(args: argparse.Namespace, client: RunnerBrokerClient) -> dict[str, Any]:
    task = client.get_task(args.task_id)
    payload_file = getattr(args, "payload_file", None)
    if payload_file:
        payload = load_json_file(payload_file)
        if not isinstance(payload, dict):
            raise ValueError("Task confirmation payload must be a JSON object")
        payload = dict(payload)
        payload.setdefault("execution_mode", args.execution_mode)
    else:
        if not clean_text(args.title) or not clean_text(args.description):
            raise ValueError("confirm-task requires --title and --description unless --payload-file is used")
        payload = {
            "title": resolved_confirm_title_from_task(task, args.title),
            "description": args.description,
            "contract": draft_contract_from_task(task, args.description),
            "execution_mode": args.execution_mode,
        }
    task = client.confirm_task(args.task_id, payload)
    return {
        "ok": True,
        "task": task,
    }


def resolved_confirm_title(client: RunnerBrokerClient, task_id: str, requested_title: str) -> str:
    try:
        task = client.get_task(task_id)
    except Exception:
        return requested_title
    return resolved_confirm_title_from_task(task, requested_title)


def resolved_confirm_title_from_task(task: dict[str, Any], requested_title: str) -> str:
    draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
    submitted_title = str(task.get("title") or "").strip()
    draft_title = str(draft.get("title") or "").strip()
    requested = str(requested_title or "").strip()
    if submitted_title and draft_title and requested == draft_title:
        return submitted_title
    return requested_title


def command_cancel_task(args: argparse.Namespace, client: RunnerBrokerClient) -> dict[str, Any]:
    client.cancel_task(args.task_id)
    return {
        "ok": True,
        "task_id": args.task_id,
        "cancelled": True,
    }


def command_list_tasks(args: argparse.Namespace, client: RunnerBrokerClient) -> dict[str, Any]:
    tasks = client.list_tasks()
    return {
        "ok": True,
        "tasks": tasks,
    }


def command_get_task(args: argparse.Namespace, client: RunnerBrokerClient) -> dict[str, Any]:
    return {
        "ok": True,
        "task": client.get_task(args.task_id),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TaskHub runner CLI for Codex skill calls.")
    parser.add_argument("--runtime-config", help="Path to runner_runtime.json. Defaults to taskhub-codex-runner/runtime/runner_runtime.json.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish-task", help="Create a TaskHub task request and print draft tasks.")
    publish.add_argument("--payload-file", help="JSON file containing the full TaskHub task request payload.")
    publish.add_argument("--source-type", default="business_system")
    publish.add_argument("--title", default="")
    publish.add_argument("--content", default="")
    publish.add_argument("--metadata-json", default="{}")
    publish.add_argument("--task-type", choices=["auto_planning", "manual_orchestration"], default="")
    publish.add_argument("--workflow-id", default="", help="Workflow template id. Enables manual orchestration mode.")
    publish.add_argument("--attachment-id", action="append", default=[], help="Task attachment id. Repeat for multiple attachments.")
    publish.set_defaults(handler=command_publish_task)

    confirm = subparsers.add_parser("confirm-task", help="Confirm a draft task and start execution.")
    confirm.add_argument("--task-id", required=True)
    confirm.add_argument("--payload-file", help="JSON file containing the full TaskConfirm payload.")
    confirm.add_argument("--title", default="")
    confirm.add_argument("--description", default="")
    confirm.add_argument("--execution-mode", choices=["sync", "async"], default="async")
    confirm.set_defaults(handler=command_confirm_task)

    cancel = subparsers.add_parser("cancel-task", help="Cancel an unconfirmed draft task.")
    cancel.add_argument("--task-id", required=True)
    cancel.set_defaults(handler=command_cancel_task)

    list_tasks = subparsers.add_parser("list-tasks", help="List TaskHub tasks.")
    list_tasks.set_defaults(handler=command_list_tasks)

    get_task = subparsers.add_parser("get-task", help="Get one TaskHub task.")
    get_task.add_argument("--task-id", required=True)
    get_task.set_defaults(handler=command_get_task)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        runtime_config = load_runner_runtime_config(path=args.runtime_config)
        client = taskhub_client(runtime_config)
        print_json(args.handler(args, client))
        return 0
    except Exception as exc:
        print_json(error_payload(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
