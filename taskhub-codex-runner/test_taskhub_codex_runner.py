from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = Path(__file__).with_name("taskhub_codex_runner.py")
SPEC = importlib.util.spec_from_file_location("taskhub_codex_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["taskhub_codex_runner"] = runner
SPEC.loader.exec_module(runner)

CLI_PATH = Path(__file__).with_name("runner_cli.py")
CLI_SPEC = importlib.util.spec_from_file_location("runner_cli", CLI_PATH)
runner_cli = importlib.util.module_from_spec(CLI_SPEC)
assert CLI_SPEC and CLI_SPEC.loader
sys.modules["runner_cli"] = runner_cli
CLI_SPEC.loader.exec_module(runner_cli)


class TaskHubCodexRunnerTests(unittest.TestCase):
    def test_needs_human_codex_result_is_not_auto_submittable(self) -> None:
        result = runner.parse_codex_result(
            '{"action": "needs_human", "decision": "need_more_info", "output": "需要人工确认预算", "questions": ["预算是否接受？"]}'
        )

        self.assertFalse(runner.should_auto_submit(result))

    def test_submit_approved_result_is_auto_submittable(self) -> None:
        result = runner.parse_codex_result('{"action": "submit", "decision": "approved", "output": "方案可通过"}')

        self.assertTrue(runner.should_auto_submit(result))

    def test_manual_choice_builds_approved_result(self) -> None:
        result = runner.manual_result_from_choice("1", "方案可以，继续执行")

        self.assertEqual(
            result,
            {
                "action": "submit",
                "decision": "approved",
                "output": "方案可以，继续执行",
                "handled_by": "local_human_via_runner",
            },
        )

    def test_manual_skip_returns_none(self) -> None:
        self.assertIsNone(runner.manual_result_from_choice("3", "暂不处理"))

    def test_install_skill_copies_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")

            message = runner.install_skill(source, target)

            self.assertIn("skill installed", message)
            self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "---\nname: demo\n---\n")

    def test_install_skill_does_not_overwrite_without_auto_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "SKILL.md").write_text("source", encoding="utf-8")
            (target / "SKILL.md").write_text("target", encoding="utf-8")

            message = runner.install_skill(source, target, auto_update=False)

            self.assertIn("already installed", message)
            self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "target")

    def test_write_skill_runtime_config_persists_runner_startup_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "taskhub-codex"

            runtime_path = runner.write_skill_runtime_config(
                target,
                {
                    "user_id": "王大锤",
                    "runner_id": "local-codex-runner",
                    "runner_cli_path": "taskhub-codex-runner/runner_cli.py",
                },
            )

            self.assertEqual(runtime_path, target / "taskhub_runtime.json")
            self.assertEqual(
                runtime_path.read_text(encoding="utf-8"),
                '{\n  "user_id": "王大锤",\n  "runner_id": "local-codex-runner",\n  "runner_cli_path": "taskhub-codex-runner/runner_cli.py"\n}\n',
            )

    def test_runtime_paths_are_fixed_under_runner_directory(self) -> None:
        paths = runner.runtime_paths()

        self.assertEqual(paths["runtime_dir"], MODULE_PATH.parent / "runtime")
        self.assertEqual(paths["pid_file"], MODULE_PATH.parent / "runtime" / "runner.pid")
        self.assertEqual(paths["log_file"], MODULE_PATH.parent / "runtime" / "runner.log")

    def test_build_skill_runtime_config_omits_taskhub_server_url_and_uses_dynamic_cli_path(self) -> None:
        config = runner.RunnerConfig(
            server_url="http://192.168.170.18:8000",
            user_id="root",
            runner_id="local-codex-runner",
            codex_command=["codex", "exec"],
            poll_interval_seconds=5,
            codex_timeout_seconds=300,
            once=False,
            dry_run=False,
            auto_submit=True,
            auto_install_skill=True,
            auto_update_skill=False,
            codex_skill_name="taskhub-codex",
            ui=False,
            ui_host="127.0.0.1",
            ui_port=8787,
        )

        runtime_config = runner.build_skill_runtime_config(config)

        self.assertNotIn("server_url", runtime_config)
        self.assertNotIn("runner_cli_command", runtime_config)
        self.assertEqual(runtime_config["runner_cli_path"], str(MODULE_PATH.parent / "runner_cli.py"))

    def test_install_runner_cli_command_writes_executable_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"

            wrapper = runner.install_runner_cli_command(bin_dir, MODULE_PATH.parent)

            self.assertEqual(wrapper, bin_dir / "taskhub-runner-cli")
            self.assertTrue(wrapper.exists())
            self.assertTrue(wrapper.stat().st_mode & 0o111)
            content = wrapper.read_text(encoding="utf-8")
            self.assertIn(str(MODULE_PATH.parent), content)
            self.assertIn("runner_cli.py", content)

    def test_write_runner_runtime_config_persists_taskhub_server_url_outside_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = runner.write_runner_runtime_config(
                Path(temp_dir),
                {
                    "server_url": "http://192.168.170.18:8000",
                    "user_id": "root",
                    "runner_id": "local-codex-runner",
                },
            )

            self.assertEqual(runtime_path, Path(temp_dir) / "runtime" / "runner_runtime.json")
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["server_url"], "http://192.168.170.18:8000")

    def test_create_task_request_proxy_calls_taskhub_once(self) -> None:
        client = runner.TaskHubClient("http://taskhub.local")
        client._request = Mock(return_value={"request_id": "req_1", "tasks": []})  # type: ignore[method-assign]

        result = client.create_task_request({"title": "测试", "content": "帮我处理任务"})

        self.assertEqual(result["request_id"], "req_1")
        client._request.assert_called_once_with("POST", "/api/v1/tasks/requests", {"title": "测试", "content": "帮我处理任务"})

    def test_runner_status_exposes_pending_manual_subtasks(self) -> None:
        state = runner.RunnerState()
        state.add_pending_manual(
            "sub_1",
            {"id": "sub_1", "title": "人工确认方案"},
            {"action": "needs_human", "decision": "need_more_info", "output": "需要人确认"},
        )

        snapshot = state.snapshot()

        self.assertEqual(snapshot["pending_manual_count"], 1)
        self.assertEqual(snapshot["pending_manual"][0]["subtask"]["title"], "人工确认方案")
        self.assertEqual(snapshot["pending_manual"][0]["codex_result"]["output"], "需要人确认")

    def test_runner_state_can_peek_before_removing_pending_manual(self) -> None:
        state = runner.RunnerState()
        state.add_pending_manual("sub_1", {"id": "sub_1", "title": "人工确认方案"}, {"output": "建议"})

        pending = state.get_pending_manual("sub_1")

        self.assertEqual(pending["subtask"]["title"], "人工确认方案")
        self.assertEqual(state.snapshot()["pending_manual_count"], 1)

        removed = state.remove_pending_manual("sub_1")

        self.assertEqual(removed["subtask"]["id"], "sub_1")
        self.assertEqual(state.snapshot()["pending_manual_count"], 0)

    def test_manual_web_submit_can_force_submit_when_auto_submit_is_disabled(self) -> None:
        config = runner.RunnerConfig(
            server_url="http://taskhub.local",
            user_id="root",
            runner_id="local-codex-runner",
            codex_command=["codex", "exec"],
            poll_interval_seconds=5,
            codex_timeout_seconds=300,
            once=False,
            dry_run=False,
            auto_submit=False,
            auto_install_skill=False,
            auto_update_skill=False,
            codex_skill_name="taskhub-codex",
            ui=True,
            ui_host="127.0.0.1",
            ui_port=8787,
        )
        task_runner = runner.TaskHubCodexRunner(config)
        task_runner.taskhub.submit_result = Mock(return_value={})  # type: ignore[method-assign]

        task_runner.submit_subtask_result(
            "sub_1",
            {"decision": "approved", "output": "人工确认通过"},
            "{}",
            force_submit=True,
        )

        task_runner.taskhub.submit_result.assert_called_once()

    def test_cli_formats_publish_task_response_for_codex(self) -> None:
        raw = {
            "request_id": "req_1",
            "tasks": [
                {
                    "id": "task_1",
                    "title": "用户提交标题",
                    "draft": {
                        "title": "识别出的任务清单",
                        "description": "- 查询客户需求\n- 管理员确认",
                    },
                }
            ],
        }

        formatted = runner_cli.format_task_request_response(raw)

        self.assertEqual(
            formatted,
            {
                "ok": True,
                "request_id": "req_1",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "submitted_title": "用户提交标题",
                        "draft_title": "识别出的任务清单",
                        "draft_description": "- 查询客户需求\n- 管理员确认",
                    }
                ],
            },
        )

    def test_cli_error_output_is_json_without_retry(self) -> None:
        payload = runner_cli.error_payload("TaskHub API failed: 500 Internal Server Error")

        self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False))["ok"], False)
        self.assertIn("500", payload["error"])

    def test_cli_loads_taskhub_config_from_runner_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            (runtime_dir / "runner_runtime.json").write_text(
                json.dumps(
                    {
                        "server_url": "http://192.168.170.18:8000",
                        "user_id": "root",
                        "runner_id": "local-codex-runner",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = runner_cli.load_runner_runtime_config(root)

            self.assertEqual(config["server_url"], "http://192.168.170.18:8000")


if __name__ == "__main__":
    unittest.main()
