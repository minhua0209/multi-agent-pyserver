from __future__ import annotations

import importlib.util
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


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


def make_runner_config(**overrides):
    values = {
        "server_url": "http://taskhub.local",
        "user_id": "root",
        "runner_id": "local-codex-runner",
        "codex_command": ["codex", "exec"],
        "poll_interval_seconds": 5,
        "codex_timeout_seconds": 300,
        "once": True,
        "dry_run": False,
        "auto_submit": True,
        "auto_install_skill": False,
        "auto_update_skill": False,
        "codex_skill_name": "taskhub-codex",
        "ui": False,
        "ui_host": "127.0.0.1",
        "ui_port": 8787,
    }
    values.update(overrides)
    return runner.RunnerConfig(**values)


class TaskHubCodexRunnerTests(unittest.TestCase):
    def test_runner_distribution_uses_backend_user_id_examples(self) -> None:
        runner_dir = MODULE_PATH.parent
        start_script = (runner_dir / "start_runner.sh").read_text(encoding="utf-8")
        skill_text = (runner_dir / "skill" / "taskhub-codex" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn('TASKHUB_USER_ID="root"', start_script)
        self.assertNotIn("王大锤", start_script)
        self.assertNotIn("王大锤", skill_text)

    def test_runner_config_defaults_to_root_user_id(self) -> None:
        with patch.dict(runner.os.environ, {}, clear=True):
            config = runner.load_config(None)

        self.assertEqual(config.user_id, "root")

    def test_runner_codex_defaults_to_runner_scoped_full_access(self) -> None:
        with patch.dict(runner.os.environ, {}, clear=True):
            config = runner.load_config(None)

        self.assertEqual(
            config.codex_command,
            [
                "codex",
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--ephemeral",
            ],
        )

    def test_runner_codex_command_can_still_be_explicitly_overridden(self) -> None:
        with patch.dict(
            runner.os.environ,
            {"TASKHUB_CODEX_COMMAND": "codex exec --sandbox read-only"},
            clear=True,
        ):
            config = runner.load_config(None)

        self.assertEqual(config.codex_command, ["codex", "exec", "--sandbox", "read-only"])

    def test_web_console_polling_preserves_manual_input_elements(self) -> None:
        html = runner.build_console_html()

        self.assertIn("function createPendingItem(item)", html)
        self.assertIn("div.dataset.subtaskId = subtask.id", html)
        self.assertNotIn("pending.innerHTML = '';", html)

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
                    "user_id": "user_001",
                    "runner_id": "local-codex-runner",
                    "runner_cli_path": "taskhub-codex-runner/runner_cli.py",
                },
            )

            self.assertEqual(runtime_path, target / "taskhub_runtime.json")
            self.assertEqual(
                runtime_path.read_text(encoding="utf-8"),
                '{\n  "user_id": "user_001",\n  "runner_id": "local-codex-runner",\n  "runner_cli_path": "taskhub-codex-runner/runner_cli.py"\n}\n',
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
        client = runner.TaskHubClient("http://taskhub.local", "root")
        client._request = Mock(return_value={"request_id": "req_1", "tasks": []})  # type: ignore[method-assign]

        result = client.create_task_request({"title": "测试", "content": "帮我处理任务"})

        self.assertEqual(result["request_id"], "req_1")
        client._request.assert_called_once_with("POST", "/api/v1/tasks/requests", {"title": "测试", "content": "帮我处理任务"})

    def test_get_current_user_calls_current_user_endpoint(self) -> None:
        client = runner.TaskHubClient("http://taskhub.local", "root")
        client._request = Mock(return_value={"id": "root", "name": "管理员"})  # type: ignore[method-assign]

        current_user = client.get_current_user()

        self.assertEqual(current_user["id"], "root")
        client._request.assert_called_once_with("GET", "/api/v1/users/current")

    def test_taskhub_client_sends_user_id_header_on_every_request(self) -> None:
        captured_request = None

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
                return None

            def read(self) -> bytes:
                return b'{"id": "root"}'

        def fake_urlopen(req, timeout):
            nonlocal captured_request
            captured_request = req
            self.assertEqual(timeout, 30)
            return FakeResponse()

        client = runner.TaskHubClient("http://taskhub.local", "root")
        with patch.object(runner.request, "urlopen", fake_urlopen):
            client.get_current_user()

        self.assertIsNotNone(captured_request)
        headers = {key.lower(): value for key, value in captured_request.header_items()}
        self.assertEqual(headers["x-user-id"], "root")

    def test_runner_start_fails_before_setup_when_current_user_does_not_match(self) -> None:
        task_runner = runner.TaskHubCodexRunner(make_runner_config(user_id="user_001"))
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "user_002"})  # type: ignore[method-assign]
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=[])  # type: ignore[method-assign]

        with patch.object(runner, "write_runner_runtime_config") as write_runtime:
            with self.assertRaisesRegex(RuntimeError, "configured user_id=user_001.*current user id=user_002"):
                task_runner.run_forever()

        write_runtime.assert_not_called()

    def test_runner_poll_fails_when_current_user_does_not_match(self) -> None:
        task_runner = runner.TaskHubCodexRunner(make_runner_config(user_id="user_001"))
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "user_002"})  # type: ignore[method-assign]
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=[])  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "configured user_id=user_001.*current user id=user_002"):
            task_runner.poll_once()

        task_runner.taskhub.poll_human_subtasks.assert_not_called()

    def test_runner_validates_matching_user_once_before_polling(self) -> None:
        task_runner = runner.TaskHubCodexRunner(make_runner_config(user_id="user_001"))
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "user_001"})  # type: ignore[method-assign]
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=[])  # type: ignore[method-assign]

        self.assertFalse(task_runner.poll_once())
        self.assertFalse(task_runner.poll_once())

        task_runner.taskhub.get_current_user.assert_called_once_with()
        self.assertEqual(task_runner.taskhub.poll_human_subtasks.call_count, 2)

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

    def test_auto_submit_disabled_queues_approved_codex_result_in_web_console(self) -> None:
        task_runner = runner.TaskHubCodexRunner(
            make_runner_config(auto_submit=False, ui=True)
        )
        subtask = {"id": "sub_1", "title": "审核分析文档"}
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "root"})  # type: ignore[method-assign]
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=[subtask])  # type: ignore[method-assign]
        task_runner.find_task_context = Mock(return_value={})  # type: ignore[method-assign]
        task_runner.codex.run = Mock(  # type: ignore[method-assign]
            return_value='{"action":"submit","decision":"approved","output":"建议通过"}'
        )
        task_runner.taskhub.submit_result = Mock(return_value={})  # type: ignore[method-assign]

        self.assertTrue(task_runner.poll_once())

        snapshot = task_runner.state.snapshot()
        self.assertEqual(snapshot["pending_manual_count"], 1)
        self.assertEqual(snapshot["pending_manual"][0]["subtask"]["id"], "sub_1")
        task_runner.taskhub.submit_result.assert_not_called()

    def test_background_runner_claims_all_available_human_subtasks_without_waiting_for_codex(self) -> None:
        task_runner = runner.TaskHubCodexRunner(
            make_runner_config(once=False, auto_submit=False, ui=True)
        )
        subtasks = [
            {"id": "sub_1", "title": "审核文档一"},
            {"id": "sub_2", "title": "审核文档二"},
        ]
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "root"})  # type: ignore[method-assign]
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=subtasks)  # type: ignore[method-assign]
        started_threads = []

        class FakeThread:
            def __init__(self, *, target, args, name, daemon):
                self.target = target
                self.args = args
                self.name = name
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        with patch.object(runner.threading, "Thread", FakeThread):
            self.assertTrue(task_runner.poll_once())

        snapshot = task_runner.state.snapshot()
        self.assertEqual(snapshot["pending_manual_count"], 2)
        self.assertEqual(len(started_threads), 2)
        self.assertEqual(task_runner.local_claimed, {"sub_1", "sub_2"})

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
                        "goal": "完成客户需求分析和确认",
                        "deliverable_goal": "形成可评审的客户需求结论",
                        "deliverable_kind": "text",
                        "deliverable_requirements": ["包含需求摘要"],
                        "success_criteria": ["管理员完成确认"],
                        "requires_human_acceptance": True,
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
                        "draft_contract": {
                            "goal": "完成客户需求分析和确认",
                            "deliverable_goal": "形成可评审的客户需求结论",
                            "deliverable_kind": "text",
                            "deliverable_format": None,
                            "deliverable_filename": "",
                            "deliverable_requirements": [],
                            "success_criteria": [
                                {"id": "", "description": "包含需求摘要"},
                                {"id": "", "description": "管理员完成确认"},
                            ],
                            "requires_human_acceptance": True,
                        },
                    }
                ],
            },
        )

    def test_cli_publish_supports_workflow_and_attachments(self) -> None:
        client = Mock()
        client.create_task_request.return_value = {"request_id": "req_1", "tasks": []}
        args = argparse.Namespace(
            payload_file=None,
            source_type="business_system",
            title="流程任务",
            content="按流程处理附件",
            metadata_json='{"channel": "codex"}',
            task_type="",
            workflow_id="workflow_1",
            attachment_id=["attachment_1", "attachment_2"],
        )

        result = runner_cli.command_publish_task(args, client)

        self.assertTrue(result["ok"])
        client.create_task_request.assert_called_once_with(
            {
                "source_type": "business_system",
                "title": "流程任务",
                "content": "按流程处理附件",
                "task_type": "manual_orchestration",
                "attachment_ids": ["attachment_1", "attachment_2"],
                "metadata": {
                    "channel": "codex",
                    "execution_mode": "workflow_template",
                    "workflow_id": "workflow_1",
                    "attachment_ids": ["attachment_1", "attachment_2"],
                },
            }
        )

    def test_cli_confirm_preserves_submitted_title_when_codex_passes_draft_title(self) -> None:
        client = Mock()
        client.get_task.return_value = {
            "id": "task_1",
            "title": "用户提交标题",
            "draft": {"title": "识别出的任务清单"},
        }
        client.confirm_task.return_value = {"id": "task_1", "title": "用户提交标题"}
        args = argparse.Namespace(
            task_id="task_1",
            title="识别出的任务清单",
            description="- 查询客户需求\n- 管理员确认",
            execution_mode="async",
            payload_file=None,
        )

        result = runner_cli.command_confirm_task(args, client)

        self.assertTrue(result["ok"])
        client.confirm_task.assert_called_once_with(
            "task_1",
            {
                "title": "用户提交标题",
                "description": "- 查询客户需求\n- 管理员确认",
                "contract": {
                    "goal": "- 查询客户需求\n- 管理员确认",
                    "deliverable_goal": "形成可评审的用户提交标题交付物",
                    "deliverable_kind": "text",
                    "deliverable_format": None,
                    "deliverable_filename": "",
                    "deliverable_requirements": [],
                    "success_criteria": [
                        {
                            "id": "",
                            "description": "交付结果满足：- 查询客户需求\n- 管理员确认",
                        }
                    ],
                    "requires_human_acceptance": False,
                },
                "execution_mode": "async",
            },
        )

    def test_cli_confirm_uses_full_payload_file(self) -> None:
        client = Mock()
        client.get_task.return_value = {"id": "task_1", "title": "任务"}
        client.confirm_task.return_value = {"id": "task_1", "title": "任务"}
        payload = {
            "title": "任务",
            "description": "确认后的任务清单",
            "contract": {
                "goal": "完成报告",
                "deliverable_goal": "生成报告文件",
                "deliverable_kind": "file",
                "deliverable_format": "markdown",
                "deliverable_filename": "report.md",
                "deliverable_requirements": [],
                "success_criteria": [{"id": "", "description": "报告内容完整"}],
                "requires_human_acceptance": False,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            payload_path = Path(temp_dir) / "confirm.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            args = argparse.Namespace(
                task_id="task_1",
                title="",
                description="",
                execution_mode="async",
                payload_file=str(payload_path),
            )

            result = runner_cli.command_confirm_task(args, client)

        self.assertTrue(result["ok"])
        client.confirm_task.assert_called_once_with(
            "task_1",
            {**payload, "execution_mode": "async"},
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

    def test_cli_runtime_client_uses_and_validates_configured_user_id(self) -> None:
        client = Mock()
        client.get_current_user.return_value = {"id": "user_001"}

        with patch.object(runner_cli, "RunnerBrokerClient", return_value=client) as client_class:
            with patch.dict(runner_cli.os.environ, {"TASKHUB_CLI_TIMEOUT_SECONDS": "12"}):
                result = runner_cli.taskhub_client(
                    {"user_id": "user_001", "ipc_dir": "/tmp/taskhub-ipc"}
                )

        self.assertIs(result, client)
        client_class.assert_called_once_with(Path("/tmp/taskhub-ipc"), timeout_seconds=12.0)
        client.get_current_user.assert_called_once_with()

    def test_cli_runtime_client_rejects_mismatched_user_id(self) -> None:
        client = Mock()
        client.get_current_user.return_value = {"id": "user_002"}

        with patch.object(runner_cli, "RunnerBrokerClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "configured user_id=user_001.*current user id=user_002"):
                runner_cli.taskhub_client(
                    {"user_id": "user_001", "ipc_dir": "/tmp/taskhub-ipc"}
                )

    def test_cli_runtime_client_requires_user_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "user_id is missing"):
            runner_cli.taskhub_client({"ipc_dir": "/tmp/taskhub-ipc"})

    def test_runner_command_broker_handles_cli_request_without_cli_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ipc_dir = Path(temp_dir) / "ipc"
            paths = {
                "runtime_dir": Path(temp_dir),
                "pid_file": Path(temp_dir) / "runner.pid",
                "log_file": Path(temp_dir) / "runner.log",
                "runner_runtime_file": Path(temp_dir) / "runner_runtime.json",
                "ipc_dir": ipc_dir,
                "ipc_requests_dir": ipc_dir / "requests",
                "ipc_responses_dir": ipc_dir / "responses",
            }
            task_runner = runner.TaskHubCodexRunner(make_runner_config())
            task_runner.taskhub = Mock()
            task_runner.taskhub.get_current_user.return_value = {
                "id": "root",
                "name": "管理员",
            }
            with patch.object(runner, "runtime_paths", return_value=paths):
                task_runner.start_command_broker()
                client = runner_cli.RunnerBrokerClient(ipc_dir, timeout_seconds=2)

                current_user = client.get_current_user()

                task_runner.stop_command_broker()
            self.assertEqual(current_user["id"], "root")
            task_runner.taskhub.get_current_user.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
