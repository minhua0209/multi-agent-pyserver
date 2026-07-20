from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.core.enums import ArtifactKind, ArtifactSourceType, ArtifactValidationStatus
from app.core.models import Artifact, SubTask, Task, ToolExecutionResult, new_id, utc_now


class ArtifactRegistrationClosedError(RuntimeError):
    pass


class ArtifactSourceIdentityError(ValueError):
    pass


class ArtifactService:
    def register_task_output(self, task: Task, output: str) -> Artifact | None:
        content = output.strip()
        if not content:
            return None
        return self._register(
            task,
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.TASK_RESULT,
            source_id=task.id,
            name="Final task output",
            content=content,
            media_type="text/plain",
            checksum=self._content_checksum(content),
            validation_status=ArtifactValidationStatus.VALID,
            validation_reason="Text content checksum calculated",
            deliverable_requirement_ids=[],
        )

    def register_subtask_output(
        self,
        task: Task,
        subtask: SubTask,
        output: str,
    ) -> Artifact | None:
        content = output.strip()
        if not content:
            return None
        return self._register(
            task,
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.SUBTASK_OUTPUT,
            source_id=subtask.id,
            name=subtask.title,
            content=content,
            media_type="text/plain",
            checksum=self._content_checksum(content),
            validation_status=ArtifactValidationStatus.VALID,
            validation_reason="Text content checksum calculated",
        )

    def register_tool_result(
        self,
        task: Task,
        subtask: SubTask,
        tool_result: ToolExecutionResult,
        ordinal: int | None = None,
    ) -> Artifact | None:
        result = tool_result.result.strip()
        if not tool_result.success or not result:
            return None
        if not tool_result.tool_execution_id and ordinal is None:
            raise ArtifactSourceIdentityError(
                "tool result requires tool_execution_id or explicit ordinal"
            )
        source_id = self._tool_source_id(task, subtask, tool_result, ordinal or 0)
        metadata = {
            "tool_name": tool_result.tool_name,
            "tool_type": tool_result.tool_type,
            "arguments": tool_result.arguments,
        }
        if tool_result.tool_type == "file_write":
            path = Path(result).expanduser().resolve()
            exists = path.is_file()
            return self._register(
                task,
                kind=ArtifactKind.FILE,
                source_type=ArtifactSourceType.TOOL_RESULT,
                source_id=source_id,
                name=path.name or tool_result.tool_name,
                uri=path.as_uri(),
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                checksum=self._file_checksum(path) if exists else "",
                validation_status=(
                    ArtifactValidationStatus.VALID
                    if exists
                    else ArtifactValidationStatus.INVALID
                ),
                validation_reason=(
                    "File exists and checksum calculated"
                    if exists
                    else "File write result does not point to an existing file"
                ),
                metadata=metadata,
            )
        return self._register(
            task,
            kind=ArtifactKind.TOOL_RESULT,
            source_type=ArtifactSourceType.TOOL_RESULT,
            source_id=source_id,
            name=tool_result.tool_name,
            content=result,
            media_type="text/plain",
            checksum=self._content_checksum(result),
            validation_status=ArtifactValidationStatus.VALID,
            validation_reason="Tool result checksum calculated",
            metadata=metadata,
        )

    @staticmethod
    def current(task: Task) -> list[Artifact]:
        if task.active_execution_id is None:
            return []
        return [
            artifact
            for artifact in task.artifacts
            if artifact.task_id == task.id
            and artifact.execution_id == task.active_execution_id
        ]

    def resolve(self, task: Task, artifact_ids: list[str]) -> list[Artifact]:
        by_id = {artifact.id: artifact for artifact in self.current(task)}
        return [by_id[artifact_id] for artifact_id in artifact_ids if artifact_id in by_id]

    def revalidate(self, task: Task, artifact: Artifact) -> Artifact:
        if artifact.kind != ArtifactKind.FILE:
            return artifact
        parsed = urlparse(artifact.uri)
        if parsed.scheme != "file":
            return self.replace_current(
                task,
                artifact.model_copy(
                    update={
                        "validation_status": ArtifactValidationStatus.INVALID,
                        "validation_reason": "File artifact URI is not a local file URI",
                    }
                ),
            )
        path = Path(unquote(parsed.path))
        if not path.is_file():
            return self.replace_current(
                task,
                artifact.model_copy(
                    update={
                        "validation_status": ArtifactValidationStatus.INVALID,
                        "validation_reason": "File artifact no longer exists",
                    }
                ),
            )
        try:
            current_checksum = self._file_checksum(path)
        except OSError as exc:
            return self.replace_current(
                task,
                artifact.model_copy(
                    update={
                        "validation_status": ArtifactValidationStatus.INVALID,
                        "validation_reason": f"File artifact could not be read: {exc}",
                    }
                ),
            )
        if not artifact.checksum or current_checksum != artifact.checksum:
            return self.replace_current(
                task,
                artifact.model_copy(
                    update={
                        "validation_status": ArtifactValidationStatus.INVALID,
                        "validation_reason": "File artifact checksum does not match registration",
                    }
                ),
            )
        if artifact.validation_status == ArtifactValidationStatus.VALID:
            return artifact
        return self.replace_current(
            task,
            artifact.model_copy(
                update={
                    "validation_status": ArtifactValidationStatus.VALID,
                    "validation_reason": "File exists and checksum matches registration",
                }
            ),
        )

    def replace_current(self, task: Task, artifact: Artifact) -> Artifact:
        execution = next(
            (
                item
                for item in task.executions
                if item.id == task.active_execution_id
            ),
            None,
        )
        if execution is None:
            raise ArtifactRegistrationClosedError(
                "artifact registration is closed: active execution is missing"
            )
        if execution.finished_at is not None:
            raise ArtifactRegistrationClosedError(
                "artifact registration is closed: execution is finished"
            )
        replacements = [
            artifact if current.id == artifact.id else current
            for current in task.artifacts
        ]
        if all(current.id != artifact.id for current in task.artifacts):
            raise ValueError("artifact does not belong to the active execution")
        candidate = task.model_copy(update={"artifacts": replacements})
        validated = Task.model_validate(candidate.model_dump(mode="python"))
        task.artifacts = validated.artifacts
        return next(current for current in task.artifacts if current.id == artifact.id)

    def _register(
        self,
        task: Task,
        *,
        kind: ArtifactKind,
        source_type: ArtifactSourceType,
        source_id: str,
        name: str,
        content: str = "",
        uri: str = "",
        media_type: str = "",
        checksum: str = "",
        validation_status: ArtifactValidationStatus,
        validation_reason: str,
        deliverable_requirement_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> Artifact | None:
        execution_id = task.active_execution_id
        execution = next(
            (item for item in task.executions if item.id == execution_id),
            None,
        )
        if execution is None:
            raise ArtifactRegistrationClosedError("artifact registration is closed: active execution is missing")
        if execution.finished_at is not None:
            raise ArtifactRegistrationClosedError("artifact registration is closed: execution is finished")
        existing = next(
            (
                artifact
                for artifact in self.current(task)
                if artifact.source_type == source_type and artifact.source_id == source_id
            ),
            None,
        )
        if existing is not None:
            return existing
        artifact = Artifact(
            id=new_id("artifact"),
            task_id=task.id,
            execution_id=execution_id,
            kind=kind,
            source_type=source_type,
            source_id=source_id,
            name=name,
            content=content,
            uri=uri,
            media_type=media_type,
            checksum=checksum,
            validation_status=validation_status,
            validation_reason=validation_reason,
            deliverable_requirement_ids=deliverable_requirement_ids or [],
            metadata=metadata or {},
            created_at=utc_now(),
        )
        task.artifacts.append(artifact)
        return artifact

    @staticmethod
    def _content_checksum(content: str) -> str:
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _file_checksum(path: Path) -> str:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _tool_source_id(
        task: Task,
        subtask: SubTask,
        tool_result: ToolExecutionResult,
        ordinal: int,
    ) -> str:
        if tool_result.tool_execution_id:
            return tool_result.tool_execution_id
        return f"{task.active_execution_id}:{subtask.id}:{ordinal}"
