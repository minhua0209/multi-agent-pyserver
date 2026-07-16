from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("taskhub_codex_runner.py")
SPEC = importlib.util.spec_from_file_location("taskhub_codex_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["taskhub_codex_runner"] = runner
SPEC.loader.exec_module(runner)


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
                    "server_url": "http://192.168.170.18:8000",
                    "user_id": "王大锤",
                    "runner_id": "local-codex-runner",
                },
            )

            self.assertEqual(runtime_path, target / "taskhub_runtime.json")
            self.assertEqual(
                runtime_path.read_text(encoding="utf-8"),
                '{\n  "server_url": "http://192.168.170.18:8000",\n  "user_id": "王大锤",\n  "runner_id": "local-codex-runner"\n}\n',
            )


if __name__ == "__main__":
    unittest.main()
