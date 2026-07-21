from __future__ import annotations

import errno
import os
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.config import get_agent_output_dir
from app.core.models import Task
from app.services import file_uri


_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
)
_SUPPORTS_SECURE_DIR_FD = bool(
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and all(
        function in getattr(os, "supports_dir_fd", ())
        for function in (os.open, os.mkdir, os.rename, os.unlink)
    )
)


@dataclass(frozen=True)
class MaterializedDeliverable:
    path: Path
    content: str
    media_type: str
    delivery_format: Literal["markdown", "text"]


class ManagedDeliveryPathError(ValueError):
    pass


class DeliverableMaterializer:
    def __init__(self, output_root: Path | None = None) -> None:
        configured_root = (
            output_root if output_root is not None else get_agent_output_dir()
        )
        self.output_root = configured_root.expanduser().resolve()

    def materialize(self, task: Task, content: str) -> MaterializedDeliverable:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("deliverable content must not be empty")

        filename = self.expected_filename(task)
        contract = task.contract
        assert contract is not None
        delivery_format = contract.deliverable_format
        task_id = task.id

        execution_id = task.active_execution_id
        if execution_id is None:
            raise ValueError("active execution is required")
        self._validate_safe_component(execution_id, label="execution id")
        if not any(execution.id == execution_id for execution in task.executions):
            raise ValueError("active execution does not exist")

        delivery_dir = self.output_root / task_id / execution_id
        target_path = delivery_dir / filename
        self._require_secure_dir_fd()
        self._materialize_with_dir_fd(
            task_id=task_id,
            execution_id=execution_id,
            filename=filename,
            content=normalized_content,
        )

        media_type = (
            "text/markdown" if delivery_format == "markdown" else "text/plain"
        )
        return MaterializedDeliverable(
            path=target_path,
            content=normalized_content,
            media_type=media_type,
            delivery_format=delivery_format,
        )

    def read_managed_delivery(self, task: Task, uri: str) -> bytes:
        self._require_secure_dir_fd()
        filename = self.expected_filename(task)
        execution_id = task.active_execution_id
        if execution_id is None:
            raise ValueError("active execution is required")
        self._validate_safe_component(execution_id, label="execution id")
        if not any(execution.id == execution_id for execution in task.executions):
            raise ValueError("active execution does not exist")

        path = file_uri.local_file_uri_to_path(uri)
        expected_path = self.output_root / task.id / execution_id / filename
        if path is None or ".." in path.parts or path != expected_path:
            raise ValueError("managed final delivery URI is invalid")
        return self._read_with_dir_fd(
            task_id=task.id,
            execution_id=execution_id,
            filename=filename,
        )

    @staticmethod
    def _require_secure_dir_fd() -> None:
        if not _SUPPORTS_SECURE_DIR_FD:
            raise OSError("secure dir_fd file operations are unavailable")

    def _materialize_with_dir_fd(
        self,
        *,
        task_id: str,
        execution_id: str,
        filename: str,
        content: str,
    ) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            root_fd = os.open(self.output_root, directory_flags)
        except OSError as exc:
            self._raise_if_unsafe_directory(exc)
            raise
        task_fd: int | None = None
        execution_fd: int | None = None
        try:
            task_fd = self._open_or_create_directory(root_fd, task_id)
            execution_fd = self._open_or_create_directory(task_fd, execution_id)
            self._write_secure_file(execution_fd, filename, content)
        finally:
            if execution_fd is not None:
                os.close(execution_fd)
            if task_fd is not None:
                os.close(task_fd)
            os.close(root_fd)

    @staticmethod
    def _open_or_create_directory(parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, 0o777, dir_fd=parent_fd)
        except FileExistsError:
            pass
        flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            directory_fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            DeliverableMaterializer._raise_if_unsafe_directory(exc)
            raise
        if stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            return directory_fd
        os.close(directory_fd)
        raise NotADirectoryError(name)

    @staticmethod
    def _raise_if_unsafe_directory(exc: OSError) -> None:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ManagedDeliveryPathError(
                "deliverable path resolves outside output root"
            ) from exc

    def _read_with_dir_fd(
        self,
        *,
        task_id: str,
        execution_id: str,
        filename: str,
    ) -> bytes:
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(self.output_root, directory_flags)
        task_fd: int | None = None
        execution_fd: int | None = None
        file_fd: int | None = None
        try:
            task_fd = os.open(task_id, directory_flags, dir_fd=root_fd)
            execution_fd = os.open(
                execution_id,
                directory_flags,
                dir_fd=task_fd,
            )
            file_flags = (
                os.O_RDONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            file_fd = os.open(filename, file_flags, dir_fd=execution_fd)
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise OSError("managed final delivery must be a regular file")
            with os.fdopen(file_fd, mode="rb") as delivery_file:
                file_fd = None
                return delivery_file.read()
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if execution_fd is not None:
                os.close(execution_fd)
            if task_fd is not None:
                os.close(task_fd)
            os.close(root_fd)

    @staticmethod
    def _create_secure_temp_file(execution_fd: int) -> tuple[str, int]:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        for _ in range(10):
            temp_name = f".deliverable.{secrets.token_hex(8)}.tmp"
            try:
                temp_fd = os.open(
                    temp_name,
                    flags,
                    0o600,
                    dir_fd=execution_fd,
                )
            except FileExistsError:
                continue
            return temp_name, temp_fd
        raise FileExistsError("could not allocate a unique temporary deliverable")

    def _write_secure_file(
        self,
        execution_fd: int,
        filename: str,
        content: str,
    ) -> None:
        temp_name, temp_fd = self._create_secure_temp_file(execution_fd)
        owned_fd: int | None = temp_fd
        try:
            temp_file = os.fdopen(
                temp_fd,
                mode="w",
                encoding="utf-8",
                newline="",
            )
            owned_fd = None
            with temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(
                temp_name,
                filename,
                src_dir_fd=execution_fd,
                dst_dir_fd=execution_fd,
            )
            temp_name = ""
        finally:
            if owned_fd is not None:
                os.close(owned_fd)
            if temp_name:
                try:
                    os.unlink(temp_name, dir_fd=execution_fd)
                except OSError:
                    pass

    def expected_filename(self, task: Task) -> str:
        contract = task.contract
        if contract is None or contract.deliverable_kind != "file":
            raise ValueError("task contract must define a file deliverable")
        delivery_format = contract.deliverable_format
        if delivery_format not in ("markdown", "text"):
            raise ValueError("file deliverable format must be markdown or text")

        task_id = task.id
        self._validate_safe_component(task_id, label="task id")
        extension = ".md" if delivery_format == "markdown" else ".txt"
        return self._resolve_filename(
            task_id=task_id,
            configured_filename=contract.deliverable_filename,
            extension=extension,
        )

    @classmethod
    def _resolve_filename(
        cls,
        *,
        task_id: str,
        configured_filename: str,
        extension: str,
    ) -> str:
        if not isinstance(configured_filename, str):
            raise ValueError("deliverable filename is not safe")
        if not configured_filename:
            filename = f"{task_id}{extension}"
            cls._validate_safe_component(filename, label="deliverable filename")
            return filename

        cls._validate_safe_component(
            configured_filename,
            label="deliverable filename",
        )
        configured_extension = Path(configured_filename).suffix.lower()
        if configured_extension and configured_extension != extension:
            raise ValueError(f"deliverable filename extension must be {extension}")
        filename = (
            configured_filename
            if configured_extension
            else f"{configured_filename}{extension}"
        )
        cls._validate_safe_component(filename, label="deliverable filename")
        return filename

    @staticmethod
    def _validate_safe_component(value: str, *, label: str) -> None:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{label} is not a safe path segment")
        try:
            encoded_value = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} must be valid UTF-8") from exc
        if len(encoded_value) > 255:
            raise ValueError(f"{label} must be at most 255 UTF-8 bytes")
        if value in {".", ".."} or value.endswith((".", " ")):
            raise ValueError(f"{label} is not a safe path segment")
        if any(
            character in _WINDOWS_INVALID_CHARACTERS
            or unicodedata.category(character) == "Cc"
            for character in value
        ):
            raise ValueError(f"{label} is not a safe path segment")

        device_basename = value.partition(".")[0].upper()
        if device_basename in _WINDOWS_DEVICE_NAMES or (
            len(device_basename) == 4
            and device_basename[:3] in {"COM", "LPT"}
            and device_basename[3] in "123456789\u00b9\u00b2\u00b3"
        ):
            raise ValueError(f"{label} is not a safe path segment")
