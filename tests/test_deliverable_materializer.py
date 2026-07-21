from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import config
from app.services import deliverable_materializer as materializer_module
from app.core.enums import (
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    utc_now,
)
from app.services.deliverable_materializer import DeliverableMaterializer


def _supports_secure_dir_fd() -> bool:
    return bool(
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and all(
            function in os.supports_dir_fd
            for function in (os.open, os.mkdir, os.rename, os.unlink)
        )
    )


def _contract(
    *,
    deliverable_kind: str = "file",
    deliverable_format: str | None = "markdown",
    deliverable_filename: str = "delivery",
) -> TaskContract:
    return TaskContract(
        goal="Prepare a deliverable",
        deliverable_goal="A reviewable file",
        deliverable_kind=deliverable_kind,
        deliverable_format=deliverable_format,
        deliverable_filename=deliverable_filename,
        success_criteria=[
            TaskContractItem(id="criterion_reviewable", description="Can be reviewed")
        ],
        confirmed_at=utc_now(),
    )


def _execution(
    *,
    task_id: str = "task_1",
    execution_id: str = "execution_1",
    attempt_no: int = 1,
    contract: TaskContract | None = None,
) -> TaskExecution:
    now = utc_now()
    return TaskExecution(
        id=execution_id,
        task_id=task_id,
        attempt_no=attempt_no,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
        started_at=now,
    )


def _task(
    *,
    task_id: str = "task_1",
    execution_id: str = "execution_1",
    contract: TaskContract | None = None,
    with_execution: bool = True,
) -> Task:
    now = utc_now()
    resolved_contract = contract if contract is not None else _contract()
    executions = (
        [
            _execution(
                task_id=task_id,
                execution_id=execution_id,
                contract=resolved_contract,
            )
        ]
        if with_execution
        else []
    )
    return Task(
        id=task_id,
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare a deliverable",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=resolved_contract,
        executions=executions,
        active_execution_id=execution_id if with_execution else None,
        created_at=now,
        updated_at=now,
    )


def test_get_agent_output_dir_defaults_to_project_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_OUTPUT_DIR", raising=False)

    assert config.PROJECT_ROOT == Path(config.__file__).resolve().parents[2]
    assert config.DEFAULT_AGENT_OUTPUT_DIR == (
        config.PROJECT_ROOT / "runtime" / "agent_outputs"
    )
    assert config.get_agent_output_dir() == config.DEFAULT_AGENT_OUTPUT_DIR.resolve()


def test_get_agent_output_dir_treats_empty_value_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OUTPUT_DIR", "   ")

    assert config.get_agent_output_dir() == config.DEFAULT_AGENT_OUTPUT_DIR.resolve()


def test_get_agent_output_dir_resolves_relative_path_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OUTPUT_DIR", "custom/outputs")

    assert config.get_agent_output_dir() == (
        config.PROJECT_ROOT / "custom" / "outputs"
    ).resolve()


def test_get_agent_output_dir_expands_absolute_user_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_OUTPUT_DIR", "~/outputs")

    assert config.get_agent_output_dir() == (home / "outputs").resolve()


def test_materializer_stores_resolved_output_root(tmp_path: Path) -> None:
    configured_root = tmp_path / "parent" / ".." / "outputs"

    materializer = DeliverableMaterializer(configured_root)

    assert materializer.output_root == configured_root.resolve()


def test_materializes_markdown_and_adds_missing_extension(tmp_path: Path) -> None:
    task = _task(contract=_contract(deliverable_filename="implementation-plan"))

    result = DeliverableMaterializer(tmp_path).materialize(task, "# Plan")

    assert result.path == tmp_path / "task_1" / "execution_1" / "implementation-plan.md"
    assert result.path.read_text(encoding="utf-8") == "# Plan"
    assert result.content == "# Plan"
    assert result.media_type == "text/markdown"
    assert result.delivery_format == "markdown"


def test_materializes_text_deliverable(tmp_path: Path) -> None:
    task = _task(
        contract=_contract(
            deliverable_format="text",
            deliverable_filename="notes.txt",
        )
    )

    result = DeliverableMaterializer(tmp_path).materialize(task, "Plain notes")

    assert result.path == tmp_path / "task_1" / "execution_1" / "notes.txt"
    assert result.path.read_text(encoding="utf-8") == "Plain notes"
    assert result.media_type == "text/plain"
    assert result.delivery_format == "text"


def test_materializer_uses_task_id_as_default_filename(tmp_path: Path) -> None:
    task = _task(contract=_contract(deliverable_filename=""))

    result = DeliverableMaterializer(tmp_path).materialize(task, "# Delivery")

    assert result.path.name == "task_1.md"


@pytest.mark.parametrize(
    ("configured_filename", "expected_filename"),
    [
        ("delivery", "delivery.md"),
        ("delivery.md", "delivery.md"),
        ("", "task_1.md"),
    ],
)
def test_materializer_exposes_expected_filename(
    configured_filename: str,
    expected_filename: str,
    tmp_path: Path,
) -> None:
    task = _task(
        contract=_contract(deliverable_filename=configured_filename)
    )
    materializer = DeliverableMaterializer(tmp_path)

    assert materializer.expected_filename(task) == expected_filename
    assert materializer.materialize(task, "Delivery").path.name == expected_filename


def test_materializer_strips_content_before_writing(tmp_path: Path) -> None:
    task = _task()

    result = DeliverableMaterializer(tmp_path).materialize(
        task,
        "  \n# Delivery\n  ",
    )

    assert result.content == "# Delivery"
    assert result.path.read_text(encoding="utf-8") == "# Delivery"


@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_materializer_rejects_empty_content(content: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="content must not be empty"):
        DeliverableMaterializer(tmp_path).materialize(_task(), content)


def test_materializer_requires_active_execution(tmp_path: Path) -> None:
    task = _task(with_execution=False)

    with pytest.raises(ValueError, match="active execution is required"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_requires_active_execution_to_exist(tmp_path: Path) -> None:
    task = _task().model_copy(update={"active_execution_id": "execution_missing"})

    with pytest.raises(ValueError, match="active execution does not exist"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_requires_file_contract(tmp_path: Path) -> None:
    task = _task(
        contract=_contract(
            deliverable_kind="text",
            deliverable_format=None,
            deliverable_filename="",
        )
    )

    with pytest.raises(ValueError, match="file deliverable"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_requires_contract(tmp_path: Path) -> None:
    task = _task().model_copy(update={"contract": None})

    with pytest.raises(ValueError, match="file deliverable"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_defensively_requires_file_format(tmp_path: Path) -> None:
    task = _task()
    task.contract = task.contract.model_copy(update={"deliverable_format": None})

    with pytest.raises(ValueError, match="format must be markdown or text"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


@pytest.mark.parametrize(
    "task_id",
    [
        "",
        " ",
        ".",
        "..",
        "../task",
        "folder/task",
        "folder\\task",
        "task:name",
        "CON",
        "LPT9",
        "task\x00",
        "task\x1f",
        "task\x7f",
        "task.",
        "task ",
    ],
)
def test_materializer_rejects_unsafe_task_id(task_id: str, tmp_path: Path) -> None:
    task = _task(task_id=task_id)

    with pytest.raises(ValueError, match="task id is not a safe path segment"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


@pytest.mark.parametrize(
    "execution_id",
    [
        "",
        " ",
        ".",
        "..",
        "../execution",
        "folder/execution",
        "folder\\execution",
        "execution|draft",
        "AUX",
        "COM1",
        "execution\x00",
        "execution\x1f",
        "execution\x7f",
        "execution.",
        "execution ",
    ],
)
def test_materializer_rejects_unsafe_execution_id(
    execution_id: str,
    tmp_path: Path,
) -> None:
    task = _task(execution_id=execution_id)

    with pytest.raises(ValueError, match="execution id is not a safe path segment"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


@pytest.mark.parametrize(
    "filename",
    [
        ".",
        "..",
        "../delivery.md",
        "folder/delivery.md",
        "folder\\delivery.md",
        "D:delivery.md",
        "delivery?.md",
        "delivery|draft.md",
        "CON.md",
        "con.MD",
        "CONIN$.md",
        "LPT9.md",
        "COM\u00b9.md",
        "delivery\x00.md",
        "delivery\x1f.md",
        "delivery\x7f.md",
        "delivery.md.",
        "delivery.md ",
        " delivery.md",
    ],
)
def test_materializer_defensively_rejects_unsafe_filename(
    filename: str,
    tmp_path: Path,
) -> None:
    task = _task()
    task.contract = task.contract.model_copy(update={"deliverable_filename": filename})

    with pytest.raises(ValueError):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_defensively_rejects_wrong_filename_extension(
    tmp_path: Path,
) -> None:
    task = _task()
    task.contract = task.contract.model_copy(
        update={"deliverable_filename": "delivery.txt"}
    )

    with pytest.raises(ValueError, match="extension must be .md"):
        DeliverableMaterializer(tmp_path).materialize(task, "Delivery")


def test_materializer_rejects_symlink_escape_from_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "root"
    outside_root = tmp_path / "outside"
    output_root.mkdir()
    outside_root.mkdir()
    (output_root / "task_1").symlink_to(outside_root, target_is_directory=True)

    with pytest.raises(ValueError, match="outside output root"):
        DeliverableMaterializer(output_root).materialize(_task(), "Delivery")


def test_materializer_creates_missing_parent_directories(tmp_path: Path) -> None:
    output_root = tmp_path / "nested" / "agent_outputs"
    assert not output_root.exists()

    result = DeliverableMaterializer(output_root).materialize(_task(), "Delivery")

    assert result.path.is_file()


@pytest.mark.parametrize(
    "configured_filename",
    [
        pytest.param("a" * 252, id="ascii"),
        pytest.param("\u4e2d" * 84, id="multibyte"),
    ],
)
def test_materializer_accepts_255_utf8_byte_final_filename(
    configured_filename: str,
    tmp_path: Path,
) -> None:
    expected_basename = f"{configured_filename}.md"
    assert len(expected_basename.encode("utf-8")) == 255

    result = DeliverableMaterializer(tmp_path).materialize(
        _task(contract=_contract(deliverable_filename=configured_filename)),
        "Delivery",
    )

    assert result.path.name == expected_basename
    assert result.path.read_text(encoding="utf-8") == "Delivery"


@pytest.mark.parametrize(
    "configured_filename",
    [
        pytest.param("a" * 253, id="ascii"),
        pytest.param("\u4e2d" * 84 + "a", id="multibyte"),
    ],
)
def test_materializer_rejects_256_utf8_byte_final_filename_without_temp_file(
    configured_filename: str,
    tmp_path: Path,
) -> None:
    expected_basename = f"{configured_filename}.md"
    target_dir = tmp_path / "task_1" / "execution_1"
    assert len(expected_basename.encode("utf-8")) == 256

    with pytest.raises(ValueError, match="255 UTF-8 bytes"):
        DeliverableMaterializer(tmp_path).materialize(
            _task(contract=_contract(deliverable_filename=configured_filename)),
            "Delivery",
        )

    assert not (target_dir / expected_basename).exists()
    assert not target_dir.exists() or not [
        path for path in target_dir.iterdir() if path.name.endswith(".tmp")
    ]


def test_materializer_rejects_lone_surrogate_filename_without_temp_file(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "task_1" / "execution_1"

    with pytest.raises(ValueError, match="valid UTF-8"):
        DeliverableMaterializer(tmp_path).materialize(
            _task(contract=_contract(deliverable_filename="\ud800")),
            "Delivery",
        )

    assert not target_dir.exists() or not [
        path for path in target_dir.iterdir() if path.name.endswith(".tmp")
    ]


def test_materializer_atomically_overwrites_without_leaving_temp_file(
    tmp_path: Path,
) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    task = _task()

    first = materializer.materialize(task, "First")
    second = materializer.materialize(task, "Second")

    assert second.path == first.path
    assert second.path.read_text(encoding="utf-8") == "Second"
    assert not [path for path in second.path.parent.iterdir() if path.name.endswith(".tmp")]


def test_materializer_fails_closed_without_secure_dir_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        materializer_module,
        "_SUPPORTS_SECURE_DIR_FD",
        False,
    )

    with pytest.raises(OSError, match="secure dir_fd"):
        DeliverableMaterializer(tmp_path).materialize(_task(), "Delivery")

    assert not (tmp_path / "task_1" / "execution_1" / "delivery.md").exists()


@pytest.mark.skipif(
    not _supports_secure_dir_fd(),
    reason="secure dir_fd materialization is unavailable",
)
def test_materializer_secure_path_fsyncs_before_dir_fd_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    events: list[str] = []
    original_fsync = os.fsync
    original_replace = os.replace

    def record_fsync(fd: int) -> None:
        assert os.fstat(fd).st_size == len("Delivery".encode("utf-8"))
        events.append("fsync")
        original_fsync(fd)

    def record_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_name = os.fspath(source)
        target_name = os.fspath(target)
        assert "/" not in source_name and "/" not in target_name
        assert src_dir_fd is not None
        assert src_dir_fd == dst_dir_fd
        events.append("replace")
        original_replace(
            source_name,
            target_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)

    result = materializer.materialize(_task(), "Delivery")

    assert events == ["fsync", "replace"]
    assert result.path.read_text(encoding="utf-8") == "Delivery"


@pytest.mark.skipif(
    not _supports_secure_dir_fd(),
    reason="secure dir_fd materialization is unavailable",
)
@pytest.mark.parametrize("replaced_level", ["task", "execution"])
def test_materializer_dir_fd_does_not_follow_replaced_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced_level: str,
) -> None:
    output_root = tmp_path / "outputs"
    task_dir = output_root / "task_1"
    execution_dir = task_dir / "execution_1"
    execution_dir.mkdir(parents=True)
    materializer = DeliverableMaterializer(output_root)
    if not hasattr(materializer, "_create_secure_temp_file"):
        pytest.fail("secure dir_fd materialization is not implemented")
    original_create_temp = materializer._create_secure_temp_file
    swapped = False

    if replaced_level == "task":
        parked_dir = output_root / "parked_task"
        sibling_dir = output_root / "sibling_task"
        sibling_execution_dir = sibling_dir / "execution_1"
        sibling_execution_dir.mkdir(parents=True)
        sibling_target = sibling_execution_dir / "delivery.md"
        parked_target = parked_dir / "execution_1" / "delivery.md"
    else:
        parked_dir = task_dir / "parked_execution"
        sibling_dir = task_dir / "sibling_execution"
        sibling_dir.mkdir()
        sibling_target = sibling_dir / "delivery.md"
        parked_target = parked_dir / "delivery.md"
    sibling_target.write_text("sentinel", encoding="utf-8")

    def replace_directory(execution_fd: int) -> tuple[str, int]:
        nonlocal swapped
        if replaced_level == "task":
            task_dir.rename(parked_dir)
            task_dir.symlink_to(sibling_dir, target_is_directory=True)
        else:
            execution_dir.rename(parked_dir)
            execution_dir.symlink_to(sibling_dir, target_is_directory=True)
        swapped = True
        return original_create_temp(execution_fd)

    monkeypatch.setattr(
        materializer,
        "_create_secure_temp_file",
        replace_directory,
    )

    materializer.materialize(_task(), "Delivery")

    assert swapped is True
    assert sibling_target.read_text(encoding="utf-8") == "sentinel"
    assert parked_target.read_text(encoding="utf-8") == "Delivery"
    assert not [
        path for path in parked_target.parent.iterdir() if path.name.endswith(".tmp")
    ]


@pytest.mark.skipif(
    not _supports_secure_dir_fd(),
    reason="secure dir_fd materialization is unavailable",
)
def test_materializer_cleans_temp_file_when_atomic_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    target_dir = tmp_path / "task_1" / "execution_1"
    target_path = target_dir / "delivery.md"

    def fail_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        assert os.fspath(target) == target_path.name
        assert src_dir_fd is not None
        assert src_dir_fd == dst_dir_fd
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        materializer.materialize(_task(), "Delivery")

    assert target_dir.is_dir()
    assert not target_path.exists()
    assert not [path for path in target_dir.iterdir() if path.name.endswith(".tmp")]


@pytest.mark.skipif(
    not _supports_secure_dir_fd(),
    reason="secure dir_fd materialization is unavailable",
)
def test_managed_read_uses_held_execution_directory_when_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "outputs"
    materializer = DeliverableMaterializer(output_root)
    task = _task()
    materialized = materializer.materialize(task, "Trusted delivery")
    task_dir = output_root / task.id
    execution_dir = task_dir / task.active_execution_id
    parked_dir = task_dir / "parked_execution"
    sibling_dir = task_dir / "sibling_execution"
    sibling_dir.mkdir()
    sibling_target = sibling_dir / "delivery.md"
    sibling_target.write_text("Untrusted delivery", encoding="utf-8")
    original_open = os.open
    swapped = False

    def replace_before_file_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if os.fspath(path) == "delivery.md" and dir_fd is not None and not swapped:
            execution_dir.rename(parked_dir)
            execution_dir.symlink_to(sibling_dir, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_before_file_open)

    content = materializer.read_managed_delivery(task, materialized.path.as_uri())

    assert swapped is True
    assert content == b"Trusted delivery"
    assert sibling_target.read_text(encoding="utf-8") == "Untrusted delivery"


@pytest.mark.skipif(
    not _supports_secure_dir_fd(),
    reason="secure dir_fd materialization is unavailable",
)
def test_managed_read_rejects_non_regular_file(tmp_path: Path) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    task = _task()
    delivery_path = tmp_path / task.id / task.active_execution_id / "delivery.md"
    delivery_path.mkdir(parents=True)

    with pytest.raises(OSError, match="regular file"):
        materializer.read_managed_delivery(task, delivery_path.as_uri())


def test_materializer_writes_rerun_to_new_execution_directory(tmp_path: Path) -> None:
    materializer = DeliverableMaterializer(tmp_path)
    task = _task()

    first = materializer.materialize(task, "First execution")
    rerun = _execution(
        task_id=task.id,
        execution_id="execution_2",
        attempt_no=2,
        contract=task.contract,
    )
    task.executions.append(rerun)
    task.active_execution_id = rerun.id

    second = materializer.materialize(task, "Second execution")

    assert second.path == tmp_path / "task_1" / "execution_2" / "delivery.md"
    assert second.path != first.path
    assert first.path.read_text(encoding="utf-8") == "First execution"
    assert second.path.read_text(encoding="utf-8") == "Second execution"
