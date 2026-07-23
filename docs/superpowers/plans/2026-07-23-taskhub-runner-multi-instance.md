# TaskHub Runner Multi-Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow multiple local TaskHub Codex Runner instances to run concurrently for different existing users, with isolated UI ports, PID files, logs, runtime configs, and IPC queues.

**Architecture:** Preserve the current default instance under `taskhub-codex-runner/runtime/`, while named instances use `runtime/instances/<instance_id>/`. The shell launcher owns instance lifecycle and validation; the Python runner receives an explicit instance ID and runtime directory, scopes all generated state to that directory, and keeps running across transient TaskHub polling failures.

**Tech Stack:** Bash, Python 3.12, `unittest`, pytest, standard-library HTTP server and filesystem IPC.

**Repository Constraint:** Do not stage or commit changes. Commit steps are intentionally omitted because the repository instructions explicitly prohibit proactive Git commits.

---

## File Map

- Modify `taskhub-codex-runner/taskhub_codex_runner.py`: instance model, runtime path selection, Skill isolation, runtime metadata, and polling retry behavior.
- Modify `taskhub-codex-runner/start_runner.sh`: instance-aware argument parsing, lifecycle management, duplicate-user protection, startup readiness checks, and `list` support.
- Modify `taskhub-codex-runner/test_taskhub_codex_runner.py`: focused Python tests for instance paths, runtime metadata, Skill behavior, and retry behavior.
- Create `taskhub-codex-runner/test_start_runner.py`: subprocess-level tests for the Bash launcher using an isolated fake runner.
- Modify `taskhub-codex-runner/README.md`: multi-instance usage, directory layout, CLI selection, and troubleshooting.
- Reference `docs/superpowers/specs/2026-07-23-taskhub-runner-multi-instance-design.md`: approved behavioral contract.

### Task 1: Add Instance And Runtime Path Primitives

**Files:**
- Modify: `taskhub-codex-runner/taskhub_codex_runner.py:17-94`
- Modify: `taskhub-codex-runner/test_taskhub_codex_runner.py:28-228`

- [ ] **Step 1: Extend the test configuration factory and add failing path-validation tests**

Add `instance_id` and `runtime_dir` to `make_runner_config()`:

```python
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
        "instance_id": "default",
        "runtime_dir": MODULE_PATH.parent / "runtime",
    }
    values.update(overrides)
    return runner.RunnerConfig(**values)
```

Replace `test_runtime_paths_are_fixed_under_runner_directory` and add the following tests:

```python
def test_default_runtime_paths_keep_legacy_layout(self) -> None:
    runtime_dir = MODULE_PATH.parent / "runtime"

    paths = runner.runtime_paths(runtime_dir)

    self.assertEqual(paths["runtime_dir"], runtime_dir)
    self.assertEqual(paths["pid_file"], runtime_dir / "runner.pid")
    self.assertEqual(paths["log_file"], runtime_dir / "runner.log")
    self.assertEqual(paths["runner_runtime_file"], runtime_dir / "runner_runtime.json")
    self.assertEqual(paths["ipc_dir"], runtime_dir / "ipc")

def test_named_instance_runtime_paths_are_isolated(self) -> None:
    runtime_dir = MODULE_PATH.parent / "runtime" / "instances" / "alice"

    paths = runner.runtime_paths(runtime_dir)

    self.assertEqual(paths["runtime_dir"], runtime_dir)
    self.assertEqual(paths["pid_file"], runtime_dir / "runner.pid")
    self.assertEqual(paths["ipc_requests_dir"], runtime_dir / "ipc" / "requests")
    self.assertEqual(paths["ipc_responses_dir"], runtime_dir / "ipc" / "responses")

def test_runtime_dir_for_instance_preserves_default_and_isolates_named(self) -> None:
    runner_dir = Path("/tmp/taskhub-runner")

    self.assertEqual(
        runner.runtime_dir_for_instance("default", runner_dir),
        runner_dir / "runtime",
    )
    self.assertEqual(
        runner.runtime_dir_for_instance("alice", runner_dir),
        runner_dir / "runtime" / "instances" / "alice",
    )

def test_validate_instance_id_rejects_path_or_whitespace(self) -> None:
    for value in ["../alice", "alice/bob", "alice bob", "", "-alice"]:
        with self.subTest(value=value):
            with self.assertRaisesRegex(ValueError, "invalid runner instance id"):
                runner.validate_instance_id(value)

def test_configure_named_instance_derives_runner_id_and_runtime_dir(self) -> None:
    config = make_runner_config(auto_install_skill=True)
    runtime_dir = Path("/tmp/taskhub-alice")

    runner.configure_instance(config, "alice", runtime_dir)

    self.assertEqual(config.instance_id, "alice")
    self.assertEqual(config.runtime_dir, runtime_dir)
    self.assertEqual(config.runner_id, "local-codex-runner-alice")
    self.assertFalse(config.auto_install_skill)

def test_configure_named_instance_preserves_explicit_runner_id(self) -> None:
    config = make_runner_config(runner_id="custom-runner")

    runner.configure_instance(config, "alice", Path("/tmp/taskhub-alice"))

    self.assertEqual(config.runner_id, "custom-runner")

def test_validate_ui_port_accepts_only_tcp_port_range(self) -> None:
    self.assertEqual(runner.validate_ui_port(8787), 8787)
    for value in [0, 65536, -1]:
        with self.subTest(value=value):
            with self.assertRaisesRegex(ValueError, "invalid UI port"):
                runner.validate_ui_port(value)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py \
  -k 'runtime_paths or runtime_dir_for_instance or validate_instance_id or configure_named_instance or validate_ui_port'
```

Expected: FAIL because `RunnerConfig` has no instance fields and the new helper functions do not exist.

- [ ] **Step 3: Implement the instance helpers and update `RunnerConfig`**

Add `re` to the standard-library imports, then add the constants and fields:

```python
import re


RUNNER_DIR = Path(__file__).resolve().parent
DEFAULT_INSTANCE_ID = "default"
DEFAULT_RUNNER_ID = "local-codex-runner"
INSTANCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


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
    instance_id: str
    runtime_dir: Path
```

Remove the original later `RUNNER_DIR = Path(__file__).resolve().parent` assignment, keep `DEFAULT_RUNNER_CODEX_COMMAND` after these declarations, and replace the fixed `runtime_paths()` with:

```python
def validate_instance_id(value: str) -> str:
    instance_id = value.strip()
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise ValueError(
            "invalid runner instance id: use 1-64 letters, numbers, underscores, or hyphens"
        )
    return instance_id


def validate_ui_port(value: int) -> int:
    if value < 1 or value > 65535:
        raise ValueError("invalid UI port: expected an integer between 1 and 65535")
    return value


def runtime_dir_for_instance(instance_id: str, runner_dir: Path = RUNNER_DIR) -> Path:
    validated = validate_instance_id(instance_id)
    if validated == DEFAULT_INSTANCE_ID:
        return runner_dir / "runtime"
    return runner_dir / "runtime" / "instances" / validated


def runtime_paths(runtime_dir: Path | None = None) -> dict[str, Path]:
    selected_runtime_dir = runtime_dir or runtime_dir_for_instance(DEFAULT_INSTANCE_ID)
    ipc_dir = selected_runtime_dir / "ipc"
    return {
        "runtime_dir": selected_runtime_dir,
        "pid_file": selected_runtime_dir / "runner.pid",
        "log_file": selected_runtime_dir / "runner.log",
        "runner_runtime_file": selected_runtime_dir / "runner_runtime.json",
        "ipc_dir": ipc_dir,
        "ipc_requests_dir": ipc_dir / "requests",
        "ipc_responses_dir": ipc_dir / "responses",
    }


def configure_instance(
    config: RunnerConfig,
    instance_id: str,
    runtime_dir: Path | None = None,
) -> RunnerConfig:
    validated = validate_instance_id(instance_id)
    config.instance_id = validated
    config.runtime_dir = (runtime_dir or runtime_dir_for_instance(validated)).resolve()
    if validated != DEFAULT_INSTANCE_ID:
        if config.runner_id == DEFAULT_RUNNER_ID:
            config.runner_id = f"{DEFAULT_RUNNER_ID}-{validated}"
        config.auto_install_skill = False
    return config
```

Update `load_config()` to use `DEFAULT_RUNNER_ID` and provide the new fields:

```python
return RunnerConfig(
    server_url=os.getenv(
        "TASKHUB_SERVER_URL",
        file_config.get("server_url", "http://127.0.0.1:8000"),
    ).rstrip("/"),
    user_id=os.getenv("TASKHUB_USER_ID", file_config.get("user_id", "root")),
    runner_id=os.getenv(
        "TASKHUB_RUNNER_ID",
        file_config.get("runner_id", DEFAULT_RUNNER_ID),
    ),
    codex_command=codex_command,
    poll_interval_seconds=int(
        os.getenv(
            "TASKHUB_POLL_INTERVAL_SECONDS",
            file_config.get("poll_interval_seconds", 5),
        )
    ),
    codex_timeout_seconds=int(
        os.getenv(
            "TASKHUB_CODEX_TIMEOUT_SECONDS",
            file_config.get("codex_timeout_seconds", 300),
        )
    ),
    once=bool_value(os.getenv("TASKHUB_RUN_ONCE", file_config.get("once", False))),
    dry_run=bool_value(os.getenv("TASKHUB_DRY_RUN", file_config.get("dry_run", False))),
    auto_submit=bool_value(
        os.getenv("TASKHUB_AUTO_SUBMIT", file_config.get("auto_submit", True))
    ),
    auto_install_skill=bool_value(
        os.getenv(
            "TASKHUB_AUTO_INSTALL_SKILL",
            file_config.get("auto_install_skill", True),
        )
    ),
    auto_update_skill=bool_value(
        os.getenv(
            "TASKHUB_AUTO_UPDATE_SKILL",
            file_config.get("auto_update_skill", False),
        )
    ),
    codex_skill_name=os.getenv(
        "TASKHUB_CODEX_SKILL_NAME",
        file_config.get("codex_skill_name", "taskhub-codex"),
    ),
    ui=bool_value(os.getenv("TASKHUB_UI", file_config.get("ui", False))),
    ui_host=os.getenv("TASKHUB_UI_HOST", file_config.get("ui_host", "127.0.0.1")),
    ui_port=validate_ui_port(
        int(os.getenv("TASKHUB_UI_PORT", file_config.get("ui_port", 8787)))
    ),
    instance_id=DEFAULT_INSTANCE_ID,
    runtime_dir=runtime_dir_for_instance(DEFAULT_INSTANCE_ID),
)
```

Update the two direct `RunnerConfig(...)` constructions in the test file to include:

```python
instance_id="default",
runtime_dir=MODULE_PATH.parent / "runtime",
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the same command from Step 2.

Expected: PASS.

### Task 2: Scope Runtime Metadata, IPC, And Global Skill Behavior

**Files:**
- Modify: `taskhub-codex-runner/taskhub_codex_runner.py:267-286, 437-472, 866-999`
- Modify: `taskhub-codex-runner/test_taskhub_codex_runner.py:179-228, 277-307, 637-663`

- [ ] **Step 1: Add failing tests for instance runtime metadata and Skill isolation**

Update the runtime config persistence test and add these tests:

```python
def test_build_runner_runtime_config_contains_instance_paths_and_ui(self) -> None:
    runtime_dir = Path("/tmp/taskhub-alice")
    config = make_runner_config(
        instance_id="alice",
        runtime_dir=runtime_dir,
        user_id="user_alice",
        runner_id="local-codex-runner-alice",
        ui=True,
        ui_port=8788,
    )

    payload = runner.build_runner_runtime_config(config)

    self.assertEqual(payload["instance_id"], "alice")
    self.assertEqual(payload["user_id"], "user_alice")
    self.assertEqual(payload["runner_id"], "local-codex-runner-alice")
    self.assertEqual(payload["ui_host"], "127.0.0.1")
    self.assertEqual(payload["ui_port"], 8788)
    self.assertEqual(payload["ipc_dir"], str(runtime_dir / "ipc"))

def test_write_runner_runtime_config_writes_directly_to_instance_directory(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_dir = Path(temp_dir) / "runtime" / "instances" / "alice"

        runtime_path = runner.write_runner_runtime_config(
            runtime_dir,
            {
                "instance_id": "alice",
                "server_url": "http://127.0.0.1:8000",
                "user_id": "user_alice",
                "runner_id": "local-codex-runner-alice",
            },
        )

        self.assertEqual(runtime_path, runtime_dir / "runner_runtime.json")

def test_named_runner_writes_own_runtime_and_skips_global_skill(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config = make_runner_config(
            instance_id="alice",
            runtime_dir=Path(temp_dir) / "alice",
            auto_install_skill=True,
            once=True,
        )
        task_runner = runner.TaskHubCodexRunner(config)
        task_runner.taskhub.get_current_user = Mock(return_value={"id": "root"})
        task_runner.taskhub.poll_human_subtasks = Mock(return_value=[])

        with patch.object(task_runner, "start_command_broker"):
            with patch.object(runner, "write_runner_runtime_config") as write_runtime:
                with patch.object(runner, "ensure_skill_installed") as install_skill:
                    task_runner.run_forever()

        write_runtime.assert_called_once_with(
            config.runtime_dir,
            runner.build_runner_runtime_config(config),
        )
        install_skill.assert_not_called()

def test_command_broker_uses_configured_instance_ipc_directory(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_dir = Path(temp_dir) / "instances" / "alice"
        task_runner = runner.TaskHubCodexRunner(
            make_runner_config(runtime_dir=runtime_dir, instance_id="alice")
        )

        with patch.object(runner.threading, "Thread") as thread_class:
            task_runner.start_command_broker()

        self.assertTrue((runtime_dir / "ipc" / "requests").is_dir())
        self.assertTrue((runtime_dir / "ipc" / "responses").is_dir())
        thread_class.assert_called_once()

def test_main_rejects_skill_install_for_named_instance(self) -> None:
    argv = [
        "taskhub_codex_runner.py",
        "--instance",
        "alice",
        "--install-skill",
    ]

    with patch.object(runner.sys, "argv", argv):
        with patch.object(runner, "ensure_skill_installed") as install_skill:
            exit_code = runner.main()

    self.assertEqual(exit_code, 2)
    install_skill.assert_not_called()
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py \
  -k 'runner_runtime_config or named_runner or command_broker_uses_configured or main_rejects_skill'
```

Expected: FAIL because runtime writes and broker paths still use the shared directory, and named Skill installation is not blocked.

- [ ] **Step 3: Make runtime config and broker paths instance-scoped**

Update `TaskHubCodexRunner.run_forever()` setup calls:

```python
def run_forever(self) -> None:
    self.validate_current_user()
    if (
        self.config.auto_install_skill
        and self.config.instance_id == DEFAULT_INSTANCE_ID
    ):
        result = ensure_skill_installed(
            self.config.codex_skill_name,
            self.config.auto_update_skill,
            runtime_config=build_skill_runtime_config(self.config),
        )
        self.log(result)
    self.start_command_broker()
    if self.config.ui:
        self.start_web_console()
    write_runner_runtime_config(
        self.config.runtime_dir,
        build_runner_runtime_config(self.config),
    )
    self.log(
        f"runner started, instance={self.config.instance_id}, "
        f"user_id={self.config.user_id}, server={self.config.server_url}"
    )
    while True:
        handled = self.poll_once()
        if self.config.once:
            return
        if not handled:
            time.sleep(self.config.poll_interval_seconds)
```

Update the broker methods:

```python
def start_command_broker(self) -> None:
    if self._command_broker_started:
        return
    paths = runtime_paths(self.config.runtime_dir)
    paths["ipc_requests_dir"].mkdir(parents=True, exist_ok=True)
    paths["ipc_responses_dir"].mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=self._run_command_broker, daemon=True)
    thread.start()
    self._command_broker_started = True
    self.log(f"command broker started: {paths['ipc_dir']}")

def _run_command_broker(self) -> None:
    paths = runtime_paths(self.config.runtime_dir)
    requests_dir = paths["ipc_requests_dir"]
    responses_dir = paths["ipc_responses_dir"]
    while not self._command_broker_stop.is_set():
        handled = False
        for request_path in sorted(requests_dir.glob("*.json")):
            processing_path = request_path.with_suffix(".processing")
            try:
                request_path.replace(processing_path)
            except FileNotFoundError:
                continue
            handled = True
            request_id = processing_path.stem
            response = self._handle_broker_request(processing_path)
            response_path = responses_dir / f"{request_id}.json"
            temporary_path = responses_dir / f".{request_id}.{uuid.uuid4().hex}.tmp"
            temporary_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(response_path)
            processing_path.unlink(missing_ok=True)
        if not handled:
            self._command_broker_stop.wait(0.05)
```

Replace the runtime config builder and writer:

```python
def build_runner_runtime_config(config: RunnerConfig) -> dict[str, Any]:
    return {
        "instance_id": config.instance_id,
        "server_url": config.server_url,
        "user_id": config.user_id,
        "runner_id": config.runner_id,
        "ui_host": config.ui_host,
        "ui_port": config.ui_port,
        "ipc_dir": str(runtime_paths(config.runtime_dir)["ipc_dir"]),
    }


def write_runner_runtime_config(
    runtime_dir: Path,
    runtime_config: dict[str, Any],
) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / "runner_runtime.json"
    runtime_path.write_text(
        json.dumps(runtime_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_path
```

- [ ] **Step 4: Add CLI instance arguments and configure the selected instance in `main()`**

Extend `parse_args()`:

```python
parser.add_argument(
    "--instance",
    default=DEFAULT_INSTANCE_ID,
    help="Runner instance id. Defaults to default.",
)
parser.add_argument(
    "--runtime-dir",
    default=None,
    help="Instance runtime directory. Defaults from --instance.",
)
```

Immediately after `config = load_config(args.config)`, configure the instance:

```python
try:
    instance_id = validate_instance_id(args.instance)
    selected_runtime_dir = (
        Path(args.runtime_dir).expanduser().resolve()
        if args.runtime_dir
        else runtime_dir_for_instance(instance_id).resolve()
    )
    configure_instance(config, instance_id, selected_runtime_dir)
except ValueError as exc:
    print(f"[taskhub-codex-runner] {exc}", file=sys.stderr)
    return 2
```

Change UI port handling to validate `0` as invalid instead of ignoring it:

```python
if args.ui_port is not None:
    try:
        config.ui_port = validate_ui_port(args.ui_port)
    except ValueError as exc:
        print(f"[taskhub-codex-runner] {exc}", file=sys.stderr)
        return 2
```

Before the existing `if args.install_skill` branch, reject named instances:

```python
if args.install_skill and config.instance_id != DEFAULT_INSTANCE_ID:
    print(
        "[taskhub-codex-runner] named instances cannot install the global TaskHub Codex skill",
        file=sys.stderr,
    )
    return 2
```

Update the default instance install branch to write to `config.runtime_dir`:

```python
if args.install_skill:
    write_runner_runtime_config(
        config.runtime_dir,
        build_runner_runtime_config(config),
    )
    print(
        ensure_skill_installed(
            config.codex_skill_name,
            config.auto_update_skill,
            runtime_config=build_skill_runtime_config(config),
        )
    )
    return 0
```

- [ ] **Step 5: Update existing tests for the new writer signature and run the focused tests**

Replace the existing persistence test with:

```python
def test_write_runner_runtime_config_persists_taskhub_server_url_outside_skill(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_dir = Path(temp_dir) / "runtime"
        runtime_path = runner.write_runner_runtime_config(
            runtime_dir,
            {
                "server_url": "http://192.168.170.18:8000",
                "user_id": "root",
                "runner_id": "local-codex-runner",
            },
        )

        self.assertEqual(runtime_path, runtime_dir / "runner_runtime.json")
        payload = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["server_url"], "http://192.168.170.18:8000")
```

In `test_runner_command_broker_handles_cli_request_without_cli_network`, remove the `patch.object(runner, "runtime_paths", ...)` block and construct the runner with the temporary runtime directory:

```python
task_runner = runner.TaskHubCodexRunner(
    make_runner_config(runtime_dir=Path(temp_dir))
)
task_runner.taskhub = Mock()
task_runner.taskhub.get_current_user.return_value = {
    "id": "root",
    "name": "管理员",
}
task_runner.start_command_broker()
client = runner_cli.RunnerBrokerClient(ipc_dir, timeout_seconds=2)

current_user = client.get_current_user()

task_runner.stop_command_broker()
self.assertEqual(current_user["id"], "root")
task_runner.taskhub.get_current_user.assert_called_once_with()
```

Run the command from Step 2.

Expected: PASS.

### Task 3: Keep Long-Running Runners Alive Across Transient Backend Failures

**Files:**
- Modify: `taskhub-codex-runner/taskhub_codex_runner.py:155-213, 267-321`
- Modify: `taskhub-codex-runner/test_taskhub_codex_runner.py:277-307, 309-335`

- [ ] **Step 1: Add failing state-clear and retry tests**

Add:

```python
def test_runner_state_can_clear_transient_error(self) -> None:
    state = runner.RunnerState()
    state.set_error("TaskHub unavailable")

    state.clear_error()

    self.assertEqual(state.snapshot()["last_error"], "")

def test_successful_poll_clears_previous_transient_error(self) -> None:
    task_runner = runner.TaskHubCodexRunner(make_runner_config())
    task_runner.state.set_error("TaskHub unavailable")
    task_runner.taskhub.get_current_user = Mock(return_value={"id": "root"})
    task_runner.taskhub.poll_human_subtasks = Mock(return_value=[])

    self.assertFalse(task_runner.poll_once())

    self.assertEqual(task_runner.state.snapshot()["last_error"], "")

def test_long_running_runner_retries_after_poll_failure(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        task_runner = runner.TaskHubCodexRunner(
            make_runner_config(
                once=False,
                poll_interval_seconds=1,
                runtime_dir=Path(temp_dir),
            )
        )
        task_runner.validate_current_user = Mock()
        task_runner.start_command_broker = Mock()
        task_runner.poll_once = Mock(
            side_effect=[RuntimeError("connection refused"), KeyboardInterrupt()]
        )

        with patch.object(runner, "write_runner_runtime_config"):
            with patch.object(runner.time, "sleep") as sleep:
                with self.assertRaises(KeyboardInterrupt):
                    task_runner.run_forever()

        self.assertIn("connection refused", task_runner.state.snapshot()["last_error"])
        sleep.assert_called_once_with(1)
        self.assertEqual(task_runner.poll_once.call_count, 2)

def test_once_runner_propagates_poll_failure(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        task_runner = runner.TaskHubCodexRunner(
            make_runner_config(once=True, runtime_dir=Path(temp_dir))
        )
        task_runner.validate_current_user = Mock()
        task_runner.start_command_broker = Mock()
        task_runner.poll_once = Mock(side_effect=RuntimeError("connection refused"))

        with patch.object(runner, "write_runner_runtime_config"):
            with self.assertRaisesRegex(RuntimeError, "connection refused"):
                task_runner.run_forever()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py \
  -k 'clear_transient_error or successful_poll_clears or retries_after_poll_failure or once_runner_propagates'
```

Expected: FAIL because `clear_error()` and the retry loop do not exist.

- [ ] **Step 3: Implement error clearing and retry behavior**

Add to `RunnerState`:

```python
def clear_error(self) -> None:
    with self.lock:
        self.last_error = ""
```

After `poll_human_subtasks()` succeeds in `poll_once()`, clear the transient error:

```python
subtasks = self.taskhub.poll_human_subtasks(self.config.user_id)
self.state.clear_error()
```

Replace the loop at the bottom of `run_forever()` with:

```python
while True:
    try:
        handled = self.poll_once()
    except Exception as exc:
        if self.config.once:
            raise
        message = str(exc).strip() or exc.__class__.__name__
        self.state.set_error(message)
        self.log(f"poll failed, retrying: {message}", stream=sys.stderr)
        time.sleep(self.config.poll_interval_seconds)
        continue
    if self.config.once:
        return
    if not handled:
        time.sleep(self.config.poll_interval_seconds)
```

- [ ] **Step 4: Run the focused retry tests**

Run the command from Step 2.

Expected: PASS.

### Task 4: Add Instance-Aware Launcher Tests

**Files:**
- Create: `taskhub-codex-runner/test_start_runner.py`

- [ ] **Step 1: Create an isolated fake-runner test harness**

Create the file with the following complete test harness and tests:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import textwrap

import pytest


RUNNER_DIR = Path(__file__).resolve().parent
START_SCRIPT = RUNNER_DIR / "start_runner.sh"

FAKE_RUNNER_SOURCE = r'''\
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import socket
import time

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--config")
parser.add_argument("--server-url", default="http://127.0.0.1:8000")
parser.add_argument("--user-id", default="root")
parser.add_argument("--instance", default="default")
parser.add_argument("--runtime-dir", required=True)
parser.add_argument("--ui", action="store_true")
parser.add_argument("--ui-host", default="127.0.0.1")
parser.add_argument("--ui-port", type=int, default=8787)
args, _ = parser.parse_known_args()

listener = None
if args.ui:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.ui_host, args.ui_port))
    listener.listen()

runtime_dir = Path(args.runtime_dir)
runtime_dir.mkdir(parents=True, exist_ok=True)
runner_id = (
    "local-codex-runner"
    if args.instance == "default"
    else f"local-codex-runner-{args.instance}"
)
(runtime_dir / "runner_runtime.json").write_text(
    json.dumps(
        {
            "instance_id": args.instance,
            "server_url": args.server_url,
            "user_id": args.user_id,
            "runner_id": runner_id,
            "ui_host": args.ui_host,
            "ui_port": args.ui_port,
            "ipc_dir": str(runtime_dir / "ipc"),
        }
    )
    + "\n",
    encoding="utf-8",
)

running = True

def stop(_signum, _frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while running:
    time.sleep(0.05)
if listener is not None:
    listener.close()
'''


@pytest.fixture
def isolated_runner_dir(tmp_path: Path):
    script_path = tmp_path / "start_runner.sh"
    shutil.copy2(START_SCRIPT, script_path)
    script_path.chmod(0o755)
    (tmp_path / "config.example.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "taskhub_codex_runner.py").write_text(
        textwrap.dedent(FAKE_RUNNER_SOURCE),
        encoding="utf-8",
    )
    yield tmp_path
    for pid_file in (tmp_path / "runtime").glob("**/runner.pid"):
        try:
            os.kill(int(pid_file.read_text(encoding="utf-8").strip()), signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass


def run_script(runner_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "TASKHUB_AUTO_INSTALL_SKILL": "false",
        "TASKHUB_UI_OPEN_BROWSER": "false",
    }
    return subprocess.run(
        [str(runner_dir / "start_runner.sh"), *args],
        cwd=runner_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_rejects_invalid_instance_and_missing_named_ui_port(
    isolated_runner_dir: Path,
) -> None:
    invalid = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_alice",
        "--instance",
        "../alice",
        "--ui",
        "--ui-port",
        str(free_port()),
        "--background",
    )
    missing_port = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_alice",
        "--instance",
        "alice",
        "--ui",
        "--background",
    )

    assert invalid.returncode == 2
    assert "invalid runner instance id" in invalid.stderr
    assert missing_port.returncode == 2
    assert "requires --ui-port" in missing_port.stderr


def test_default_and_named_instances_are_managed_independently(
    isolated_runner_dir: Path,
) -> None:
    default_port = free_port()
    alice_port = free_port()

    default_start = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "root",
        "--ui",
        "--ui-port",
        str(default_port),
        "--background",
    )
    alice_start = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_alice",
        "--instance",
        "alice",
        "--ui",
        "--ui-port",
        str(alice_port),
        "--background",
    )

    assert default_start.returncode == 0, default_start.stderr
    assert alice_start.returncode == 0, alice_start.stderr
    assert run_script(isolated_runner_dir, "status").returncode == 0
    assert run_script(isolated_runner_dir, "status", "alice").returncode == 0

    listed = run_script(isolated_runner_dir, "list")
    assert listed.returncode == 0
    assert "default" in listed.stdout
    assert "root" in listed.stdout
    assert "alice" in listed.stdout
    assert "user_alice" in listed.stdout

    stopped = run_script(isolated_runner_dir, "stop", "alice")
    assert stopped.returncode == 0
    assert run_script(isolated_runner_dir, "status", "alice").returncode == 1
    assert run_script(isolated_runner_dir, "status").returncode == 0


def test_rejects_duplicate_active_user(isolated_runner_dir: Path) -> None:
    alice_port = free_port()
    bob_port = free_port()
    first = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_shared",
        "--instance",
        "alice",
        "--ui",
        "--ui-port",
        str(alice_port),
        "--background",
    )
    second = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_shared",
        "--instance",
        "bob",
        "--ui",
        "--ui-port",
        str(bob_port),
        "--background",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 2
    assert "already used by running instance alice" in second.stderr


def test_port_conflict_fails_and_cleans_named_pid(isolated_runner_dir: Path) -> None:
    shared_port = free_port()
    first = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_alice",
        "--instance",
        "alice",
        "--ui",
        "--ui-port",
        str(shared_port),
        "--background",
    )
    second = run_script(
        isolated_runner_dir,
        "http://127.0.0.1:8000",
        "user_bob",
        "--instance",
        "bob",
        "--ui",
        "--ui-port",
        str(shared_port),
        "--background",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "failed to start" in second.stderr
    assert not (
        isolated_runner_dir / "runtime" / "instances" / "bob" / "runner.pid"
    ).exists()


def test_stop_does_not_kill_process_from_reused_pid(isolated_runner_dir: Path) -> None:
    runtime_dir = isolated_runner_dir / "runtime" / "instances" / "alice"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "runner.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (runtime_dir / "runner_runtime.json").write_text(
        json.dumps({"instance_id": "alice", "user_id": "user_alice"}) + "\n",
        encoding="utf-8",
    )

    stopped = run_script(isolated_runner_dir, "stop", "alice")

    assert stopped.returncode == 0
    assert "stale" in stopped.stdout
    assert not (runtime_dir / "runner.pid").exists()
```

- [ ] **Step 2: Run the launcher tests and verify they fail against the current script**

Run:

```bash
pytest -q taskhub-codex-runner/test_start_runner.py
```

Expected: FAIL because the current script has one shared runtime directory and no instance parser or `list` command.

### Task 5: Implement Multi-Instance Lifecycle In `start_runner.sh`

**Files:**
- Modify: `taskhub-codex-runner/start_runner.sh:1-106`
- Test: `taskhub-codex-runner/test_start_runner.py`

- [ ] **Step 1: Replace the launcher with instance-aware lifecycle management**

Replace `start_runner.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_RUNTIME_DIR="$SCRIPT_DIR/runtime"
DEFAULT_INSTANCE_ID="default"

usage() {
  echo "Usage: $0 <TASKHUB_SERVER_URL> [USER_ID] [--instance ID] [--once] [--dry-run] [--ui] [--ui-port PORT] [--background]"
  echo "       $0 status [INSTANCE_ID]"
  echo "       $0 stop [INSTANCE_ID]"
  echo "       $0 list"
}

fail() {
  echo "TaskHub Codex runner: $1" >&2
  exit 2
}

validate_instance_id() {
  local instance_id="$1"
  if [[ ! "$instance_id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
    fail "invalid runner instance id: use 1-64 letters, numbers, underscores, or hyphens"
  fi
}

validate_ui_port() {
  local port="$1"
  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    fail "invalid UI port: expected an integer between 1 and 65535"
  fi
  local port_number=$((10#$port))
  if ((port_number < 1 || port_number > 65535)); then
    fail "invalid UI port: expected an integer between 1 and 65535"
  fi
}

runtime_dir_for_instance() {
  local instance_id="$1"
  if [[ "$instance_id" == "$DEFAULT_INSTANCE_ID" ]]; then
    printf '%s\n' "$BASE_RUNTIME_DIR"
  else
    printf '%s\n' "$BASE_RUNTIME_DIR/instances/$instance_id"
  fi
}

set_instance_paths() {
  INSTANCE_ID="$1"
  validate_instance_id "$INSTANCE_ID"
  RUNTIME_DIR="$(runtime_dir_for_instance "$INSTANCE_ID")"
  PID_FILE="$RUNTIME_DIR/runner.pid"
  LOG_FILE="$RUNTIME_DIR/runner.log"
  RUNTIME_CONFIG_FILE="$RUNTIME_DIR/runner_runtime.json"
}

read_runtime_field() {
  local runtime_file="$1"
  local field="$2"
  if [[ ! -f "$runtime_file" ]]; then
    return 0
  fi
  python3 -c 'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(data.get(sys.argv[2], ""))' "$runtime_file" "$field" 2>/dev/null || true
}

runner_process_matches() {
  local pid="$1"
  local runtime_dir="$2"
  local command
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  command="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *"taskhub_codex_runner.py"* && "$command" == *"--runtime-dir"*"$runtime_dir"* ]]
}

status_runner() {
  local instance_id="${1:-$DEFAULT_INSTANCE_ID}"
  set_instance_paths "$instance_id"
  if [[ ! -f "$PID_FILE" ]]; then
    echo "TaskHub Codex runner is not running: instance=$INSTANCE_ID"
    return 1
  fi
  local pid
  pid="$(<"$PID_FILE")"
  if runner_process_matches "$pid" "$RUNTIME_DIR"; then
    echo "TaskHub Codex runner is running: instance=$INSTANCE_ID pid=$pid"
    echo "Log: $LOG_FILE"
    return 0
  fi
  echo "TaskHub Codex runner stale pid removed: instance=$INSTANCE_ID pid=$pid"
  rm -f "$PID_FILE"
  return 1
}

stop_runner() {
  local instance_id="${1:-$DEFAULT_INSTANCE_ID}"
  set_instance_paths "$instance_id"
  if [[ ! -f "$PID_FILE" ]]; then
    echo "TaskHub Codex runner is not running: instance=$INSTANCE_ID"
    return 0
  fi
  local pid
  pid="$(<"$PID_FILE")"
  if runner_process_matches "$pid" "$RUNTIME_DIR"; then
    kill "$pid"
    echo "Stopped TaskHub Codex runner: instance=$INSTANCE_ID pid=$pid"
  else
    echo "TaskHub Codex runner stale pid removed: instance=$INSTANCE_ID pid=$pid"
  fi
  rm -f "$PID_FILE"
}

print_instance_row() {
  local instance_id="$1"
  local runtime_dir="$2"
  local pid_file="$runtime_dir/runner.pid"
  local runtime_file="$runtime_dir/runner_runtime.json"
  local pid="-"
  local status="stopped"
  if [[ -f "$pid_file" ]]; then
    pid="$(<"$pid_file")"
    if runner_process_matches "$pid" "$runtime_dir"; then
      status="running"
    fi
  fi
  local user_id runner_id ui_host ui_port ui_url
  user_id="$(read_runtime_field "$runtime_file" user_id)"
  runner_id="$(read_runtime_field "$runtime_file" runner_id)"
  ui_host="$(read_runtime_field "$runtime_file" ui_host)"
  ui_port="$(read_runtime_field "$runtime_file" ui_port)"
  ui_url="-"
  if [[ -n "$ui_host" && -n "$ui_port" ]]; then
    ui_url="http://$ui_host:$ui_port"
  fi
  printf '%-16s %-24s %-32s %-10s %-8s %-30s %s\n' \
    "$instance_id" "${user_id:--}" "${runner_id:--}" "$status" "$pid" "$ui_url" "$runtime_dir/runner.log"
}

list_runners() {
  printf '%-16s %-24s %-32s %-10s %-8s %-30s %s\n' \
    "INSTANCE" "USER_ID" "RUNNER_ID" "STATUS" "PID" "UI" "LOG"
  print_instance_row "$DEFAULT_INSTANCE_ID" "$BASE_RUNTIME_DIR"
  local instance_dir
  for instance_dir in "$BASE_RUNTIME_DIR"/instances/*; do
    [[ -d "$instance_dir" ]] || continue
    print_instance_row "$(basename "$instance_dir")" "$instance_dir"
  done
}

ensure_user_not_running_elsewhere() {
  local requested_user_id="$1"
  local requested_runtime_dir="$2"
  local instance_dir instance_id pid_file pid existing_user
  for instance_dir in "$BASE_RUNTIME_DIR" "$BASE_RUNTIME_DIR"/instances/*; do
    [[ -d "$instance_dir" ]] || continue
    [[ "$instance_dir" == "$requested_runtime_dir" ]] && continue
    pid_file="$instance_dir/runner.pid"
    [[ -f "$pid_file" ]] || continue
    pid="$(<"$pid_file")"
    if ! runner_process_matches "$pid" "$instance_dir"; then
      continue
    fi
    existing_user="$(read_runtime_field "$instance_dir/runner_runtime.json" user_id)"
    if [[ "$existing_user" == "$requested_user_id" ]]; then
      if [[ "$instance_dir" == "$BASE_RUNTIME_DIR" ]]; then
        instance_id="$DEFAULT_INSTANCE_ID"
      else
        instance_id="$(basename "$instance_dir")"
      fi
      fail "user_id $requested_user_id is already used by running instance $instance_id"
    fi
  done
}

if [[ "${1:-}" == "status" ]]; then
  [[ $# -le 2 ]] || fail "status accepts at most one instance id"
  status_runner "${2:-$DEFAULT_INSTANCE_ID}"
  exit $?
fi

if [[ "${1:-}" == "stop" ]]; then
  [[ $# -le 2 ]] || fail "stop accepts at most one instance id"
  stop_runner "${2:-$DEFAULT_INSTANCE_ID}"
  exit 0
fi

if [[ "${1:-}" == "list" ]]; then
  [[ $# -eq 1 ]] || fail "list does not accept extra arguments"
  list_runners
  exit 0
fi

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

TASKHUB_SERVER_URL="$1"
shift || true
TASKHUB_USER_ID="root"
if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  TASKHUB_USER_ID="$1"
  shift || true
fi

BACKGROUND=false
INSTANCE_ID="$DEFAULT_INSTANCE_ID"
UI_ENABLED=false
UI_HOST="${TASKHUB_UI_HOST:-127.0.0.1}"
UI_PORT=""
INSTALL_SKILL=false
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --background)
      BACKGROUND=true
      shift
      ;;
    --instance)
      [[ $# -ge 2 ]] || fail "--instance requires a value"
      INSTANCE_ID="$2"
      shift 2
      ;;
    --ui)
      UI_ENABLED=true
      ARGS+=("--ui")
      shift
      ;;
    --ui-host)
      [[ $# -ge 2 ]] || fail "--ui-host requires a value"
      UI_HOST="$2"
      ARGS+=("--ui-host" "$2")
      shift 2
      ;;
    --ui-port)
      [[ $# -ge 2 ]] || fail "--ui-port requires a value"
      UI_PORT="$2"
      ARGS+=("--ui-port" "$2")
      shift 2
      ;;
    --install-skill)
      INSTALL_SKILL=true
      ARGS+=("--install-skill")
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

validate_instance_id "$INSTANCE_ID"
if [[ -n "$UI_PORT" ]]; then
  validate_ui_port "$UI_PORT"
fi
if [[ "$INSTANCE_ID" != "$DEFAULT_INSTANCE_ID" && "$UI_ENABLED" == "true" && -z "$UI_PORT" ]]; then
  fail "named instance $INSTANCE_ID with --ui requires --ui-port"
fi
if [[ "$INSTANCE_ID" != "$DEFAULT_INSTANCE_ID" && "$INSTALL_SKILL" == "true" ]]; then
  fail "named instances cannot install the global TaskHub Codex skill"
fi

set_instance_paths "$INSTANCE_ID"
export TASKHUB_SERVER_URL
export TASKHUB_USER_ID
if [[ "$INSTANCE_ID" != "$DEFAULT_INSTANCE_ID" && -z "${TASKHUB_RUNNER_ID:-}" ]]; then
  export TASKHUB_RUNNER_ID="local-codex-runner-$INSTANCE_ID"
fi

COMMAND=(
  python3 "$SCRIPT_DIR/taskhub_codex_runner.py"
  --config "$SCRIPT_DIR/config.example.json"
  --server-url "$TASKHUB_SERVER_URL"
  --user-id "$TASKHUB_USER_ID"
  --instance "$INSTANCE_ID"
  --runtime-dir "$RUNTIME_DIR"
  "${ARGS[@]}"
)

mkdir -p "$RUNTIME_DIR"
if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(<"$PID_FILE")"
  if runner_process_matches "$existing_pid" "$RUNTIME_DIR"; then
    echo "TaskHub Codex runner is already running: instance=$INSTANCE_ID pid=$existing_pid"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

ensure_user_not_running_elsewhere "$TASKHUB_USER_ID" "$RUNTIME_DIR"
rm -f "$RUNTIME_CONFIG_FILE"

if [[ "$BACKGROUND" == "true" ]]; then
  nohup "${COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  pid="$!"
  printf '%s\n' "$pid" > "$PID_FILE"
  ready=false
  for _attempt in {1..100}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "TaskHub Codex runner failed to start: instance=$INSTANCE_ID log=$LOG_FILE" >&2
      tail -n 20 "$LOG_FILE" >&2 || true
      exit 1
    fi
    if [[ -f "$RUNTIME_CONFIG_FILE" ]]; then
      ready=true
      break
    fi
    sleep 0.1
  done
  if [[ "$ready" != "true" ]]; then
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "TaskHub Codex runner startup timed out: instance=$INSTANCE_ID log=$LOG_FILE" >&2
    exit 1
  fi
  echo "TaskHub Codex runner started in background: instance=$INSTANCE_ID pid=$pid"
  echo "Log: $LOG_FILE"
  if [[ "$UI_ENABLED" == "true" ]]; then
    echo "Web console: http://$UI_HOST:${UI_PORT:-8787}"
  fi
  exit 0
fi

"${COMMAND[@]}" &
pid="$!"
printf '%s\n' "$pid" > "$PID_FILE"
cleanup_foreground() {
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}
trap cleanup_foreground EXIT INT TERM
wait "$pid"
```

- [ ] **Step 2: Check Bash syntax before running behavior tests**

Run:

```bash
bash -n taskhub-codex-runner/start_runner.sh
```

Expected: exit code `0` and no output.

- [ ] **Step 3: Run launcher tests**

Run:

```bash
pytest -q taskhub-codex-runner/test_start_runner.py
```

Expected: PASS.

- [ ] **Step 4: Run existing runner tests to catch launcher compatibility regressions**

Run:

```bash
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py
```

Expected: PASS. The existing script-content assertion for `TASKHUB_USER_ID="root"` remains unchanged and must still pass.

### Task 6: Document Multi-Instance Operation

**Files:**
- Modify: `taskhub-codex-runner/README.md:13-180`

- [ ] **Step 1: Replace the fixed-runtime wording and add named-instance examples**

Add a `多实例运行` section after the existing background status/stop examples:

```markdown
## 多实例运行

同一台机器可以为多个已存在的 TaskHub 用户启动独立 Runner。每个命名实例必须使用不同的 `user_id` 和 UI 端口：

```bash
./start_runner.sh http://127.0.0.1:8000 user_alice \
  --instance alice --ui --ui-port 8787 --background

./start_runner.sh http://127.0.0.1:8000 user_bob \
  --instance bob --ui --ui-port 8788 --background
```

本机分别访问：

```text
http://127.0.0.1:8787
http://127.0.0.1:8788
```

管理实例：

```bash
./start_runner.sh status alice
./start_runner.sh stop alice
./start_runner.sh list
```

未指定 `--instance` 时仍使用默认实例和原 `runtime/` 目录。命名实例使用：

```text
taskhub-codex-runner/runtime/instances/<instance_id>/runner.pid
taskhub-codex-runner/runtime/instances/<instance_id>/runner.log
taskhub-codex-runner/runtime/instances/<instance_id>/runner_runtime.json
taskhub-codex-runner/runtime/instances/<instance_id>/ipc/
```

同一个 `user_id` 不能同时绑定多个运行实例，避免重复领取人工子任务。Runner UI 默认只监听 `127.0.0.1`，不向局域网开放。
```

- [ ] **Step 2: Document named-instance Runner CLI selection and global Skill limitation**

Add after the current Runner CLI runtime description:

```markdown
命名实例不会覆盖全局 `~/.codex/skills/taskhub-codex/taskhub_runtime.json`。需要通过某个命名实例执行 Runner CLI 时，显式选择它的运行配置：

```bash
python3 taskhub-codex-runner/runner_cli.py \
  --runtime-config taskhub-codex-runner/runtime/instances/alice/runner_runtime.json \
  list-tasks
```

全局 Codex Skill 仍只绑定默认实例；命名实例不能使用 `--install-skill`。
```

- [ ] **Step 3: Document transient backend recovery**

Add to the debugging section:

```markdown
常驻 Runner 在 TaskHub 后端短暂重启或网络暂时不可用时，会记录错误并按轮询间隔重试。首次启动时用户校验失败仍会直接退出；`--once` 模式遇到连接错误也会返回失败。
```

- [ ] **Step 4: Review README examples for fixed-path contradictions**

Run:

```bash
rg -n '固定写入|runtime/runner|8787|install-skill|多实例' taskhub-codex-runner/README.md
```

Expected: fixed paths are explicitly described as default-instance paths, named examples use unique ports, and Skill installation is documented as default-only.

### Task 7: Run Full Automated Verification

**Files:**
- Verify only; do not modify unrelated files.

- [ ] **Step 1: Run Bash syntax and both runner test modules**

Run:

```bash
bash -n taskhub-codex-runner/start_runner.sh
pytest -q taskhub-codex-runner/test_taskhub_codex_runner.py
pytest -q taskhub-codex-runner/test_start_runner.py
```

Expected: all commands pass.

- [ ] **Step 2: Run the repository test suite**

Run:

```bash
pytest -q
```

Expected: PASS. If unrelated pre-existing failures occur, record the exact failing tests and keep runner changes scoped.

- [ ] **Step 3: Inspect the final diff for scope and sensitive data**

Run:

```bash
git diff -- taskhub-codex-runner docs/superpowers/specs/2026-07-23-taskhub-runner-multi-instance-design.md docs/superpowers/plans/2026-07-23-taskhub-runner-multi-instance.md
git status --short
```

Expected: only the planned runner implementation, tests, README, design, and plan appear as this feature's changes. No API keys, credentials, private endpoints, generated runtime files, PID files, or logs are added.

### Task 8: Perform Local Two-User Acceptance

**Files:**
- Runtime files only under `taskhub-codex-runner/runtime/`. If acceptance exposes a defect, stop acceptance, add one focused failing test to the relevant earlier task, and apply only the minimal fix required by that test.

- [ ] **Step 1: Read two active existing user IDs without creating users**

With the API already running on port 8000, run:

```bash
read -r TASKHUB_TEST_USER_A TASKHUB_TEST_USER_B < <(
  curl -sS -H 'X-User-Id: root' http://127.0.0.1:8000/api/v1/users/assignable |
  python3 -c 'import json, sys; ids=[item["id"] for item in json.load(sys.stdin) if item.get("id")]; assert len(ids) >= 2, "need at least two active users"; print(ids[0], ids[1])'
)
test -n "$TASKHUB_TEST_USER_A"
test -n "$TASKHUB_TEST_USER_B"
test "$TASKHUB_TEST_USER_A" != "$TASKHUB_TEST_USER_B"
printf 'user_a=%s user_b=%s\n' "$TASKHUB_TEST_USER_A" "$TASKHUB_TEST_USER_B"
```

Expected: two different existing backend user IDs are printed. No user is created or modified.

- [ ] **Step 2: Start two named local consoles with manual submission enabled**

Run:

```bash
./taskhub-codex-runner/start_runner.sh stop

TASKHUB_AUTO_SUBMIT=false ./taskhub-codex-runner/start_runner.sh \
  http://127.0.0.1:8000 "$TASKHUB_TEST_USER_A" \
  --instance alice --ui --ui-port 8787 --background

TASKHUB_AUTO_SUBMIT=false ./taskhub-codex-runner/start_runner.sh \
  http://127.0.0.1:8000 "$TASKHUB_TEST_USER_B" \
  --instance bob --ui --ui-port 8788 --background
```

Expected: the default instance is stopped so port 8787 is available, then both named commands report successful startup and different log paths.

- [ ] **Step 3: Verify list, status, HTTP pages, and process isolation**

Run:

```bash
./taskhub-codex-runner/start_runner.sh list
./taskhub-codex-runner/start_runner.sh status alice
./taskhub-codex-runner/start_runner.sh status bob
curl -sS -o /dev/null -w 'alice=%{http_code}\n' http://127.0.0.1:8787/
curl -sS -o /dev/null -w 'bob=%{http_code}\n' http://127.0.0.1:8788/
```

Expected: `alice` and `bob` are running with different users and PIDs; both HTTP checks return `200`.

- [ ] **Step 4: Stop one instance and verify the other remains available**

Run:

```bash
./taskhub-codex-runner/start_runner.sh stop alice
./taskhub-codex-runner/start_runner.sh status bob
curl -sS -o /dev/null -w 'bob=%{http_code}\n' http://127.0.0.1:8788/
```

Expected: `alice` stops, `bob` remains running, and Bob's page returns `200`.

- [ ] **Step 5: Verify backend restart resilience**

In the terminal that currently owns port 8000, stop and relaunch the same `python app/main.py` command with its existing environment unchanged. Then run:

```bash
./taskhub-codex-runner/start_runner.sh status bob
curl -sS -o /dev/null -w 'bob=%{http_code}\n' http://127.0.0.1:8788/
tail -n 40 taskhub-codex-runner/runtime/instances/bob/runner.log
```

Expected: Bob's Runner process and UI remain alive. The log shows a transient polling error during the API restart and later successful polling after recovery.

- [ ] **Step 6: Stop the remaining acceptance instance**

Run:

```bash
./taskhub-codex-runner/start_runner.sh stop bob
./taskhub-codex-runner/start_runner.sh list
```

Expected: both named instances show `stopped`; generated logs and runtime configs remain available for inspection, while PID files are removed.
