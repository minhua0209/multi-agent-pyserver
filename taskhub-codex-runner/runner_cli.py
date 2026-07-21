#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from taskhub_codex_runner import TaskHubClient


RUNNER_DIR = Path(__file__).resolve().parent
DEFAULT_RUNNER_RUNTIME_PATH = RUNNER_DIR / "runtime" / "runner_runtime.json"


def load_runner_runtime_config(runner_dir: Path | None = None, path: str | None = None) -> dict[str, Any]:
    runtime_path = Path(path).expanduser() if path else (runner_dir or RUNNER_DIR) / "runtime" / "runner_runtime.json"
    if not runtime_path.exists():
        raise RuntimeError(f"TaskHub runner runtime config not found: {runtime_path}")
    return json.loads(runtime_path.read_text(encoding="utf-8"))


def taskhub_client(runtime_config: dict[str, Any]) -> TaskHubClient:
    server_url = str(runtime_config.get("server_url") or "").strip()
    if not server_url:
        raise RuntimeError("TaskHub server_url is missing in runner runtime config")
    user_id = str(runtime_config.get("user_id") or "").strip()
    if not user_id:
        raise RuntimeError("TaskHub user_id is missing in runner runtime config")
    client = TaskHubClient(server_url.rstrip("/"), user_id)
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


def command_publish_task(args: argparse.Namespace, client: TaskHubClient) -> dict[str, Any]:
    if args.payload_file:
        payload = load_json_file(args.payload_file)
    else:
        metadata = json.loads(args.metadata_json) if args.metadata_json else {}
        payload = {
            "source_type": args.source_type,
            "title": args.title,
            "content": args.content,
            "metadata": metadata,
        }
    return format_task_request_response(client.create_task_request(payload))


def command_confirm_task(args: argparse.Namespace, client: TaskHubClient) -> dict[str, Any]:
    title = resolved_confirm_title(client, args.task_id, args.title)
    payload = {
        "title": title,
        "description": args.description,
        "execution_mode": args.execution_mode,
    }
    task = client.confirm_task(args.task_id, payload)
    return {
        "ok": True,
        "task": task,
    }


def resolved_confirm_title(client: TaskHubClient, task_id: str, requested_title: str) -> str:
    try:
        task = client.get_task(task_id)
    except Exception:
        return requested_title
    draft = task.get("draft") if isinstance(task.get("draft"), dict) else {}
    submitted_title = str(task.get("title") or "").strip()
    draft_title = str(draft.get("title") or "").strip()
    requested = str(requested_title or "").strip()
    if submitted_title and draft_title and requested == draft_title:
        return submitted_title
    return requested_title


def command_cancel_task(args: argparse.Namespace, client: TaskHubClient) -> dict[str, Any]:
    client.cancel_task(args.task_id)
    return {
        "ok": True,
        "task_id": args.task_id,
        "cancelled": True,
    }


def command_list_tasks(args: argparse.Namespace, client: TaskHubClient) -> dict[str, Any]:
    tasks = client.list_tasks()
    return {
        "ok": True,
        "tasks": tasks,
    }


def command_get_task(args: argparse.Namespace, client: TaskHubClient) -> dict[str, Any]:
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
    publish.set_defaults(handler=command_publish_task)

    confirm = subparsers.add_parser("confirm-task", help="Confirm a draft task and start execution.")
    confirm.add_argument("--task-id", required=True)
    confirm.add_argument("--title", required=True)
    confirm.add_argument("--description", required=True)
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
