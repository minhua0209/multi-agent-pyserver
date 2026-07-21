import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.core.enums as core_enums
import app.core.models as core_models
from app.core.enums import (
    ArtifactKind,
    ArtifactSourceType,
    ArtifactValidationStatus,
    CurrentNode,
    ExecutionTriggerType,
    SourceType,
    TaskStatus,
)
from app.core.models import (
    SubTask,
    Task,
    TaskContract,
    TaskContractItem,
    TaskExecution,
    ToolExecutionResult,
    utc_now,
)
from app.services.deliverable_materializer import MaterializedDeliverable


def test_artifact_model_is_strict_and_requires_content_or_uri() -> None:
    assert hasattr(core_enums, "ArtifactKind")
    assert hasattr(core_enums, "ArtifactSourceType")
    assert hasattr(core_enums, "ArtifactValidationStatus")
    assert hasattr(core_models, "Artifact")
    artifact_class = core_models.Artifact
    base = {
        "id": "artifact_1",
        "task_id": "task_1",
        "execution_id": "execution_1",
        "kind": "text",
        "source_type": "task_result",
        "source_id": "task_1",
        "name": "Final output",
        "content": "Reviewable delivery",
        "checksum": "sha256:abc123",
        "validation_status": "valid",
        "validation_reason": "Content checksum calculated",
        "created_at": core_models.utc_now(),
    }

    artifact = artifact_class.model_validate(base)

    assert artifact.kind.value == "text"
    assert artifact.source_type.value == "task_result"
    assert artifact.validation_status.value == "valid"
    with pytest.raises(ValidationError, match="content or uri"):
        artifact_class.model_validate({**base, "content": ""})
    with pytest.raises(ValidationError, match="deliverable requirement IDs must be unique"):
        artifact_class.model_validate(
            {**base, "deliverable_requirement_ids": ["requirement_1", "requirement_1"]}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        artifact_class.model_validate({**base, "unexpected": True})


def _artifact_service():
    try:
        from app.services.artifact_service import ArtifactService
    except ModuleNotFoundError:
        pytest.fail("ArtifactService is not implemented")
    return ArtifactService()


def _file_uri_module():
    try:
        from app.services import file_uri
    except ImportError:
        pytest.fail("file URI conversion helper is not implemented")
    return file_uri


def _task_with_execution(
    *,
    input_artifacts: list[str] | None = None,
    file_delivery: bool = False,
) -> Task:
    now = utc_now()
    contract = TaskContract(
        goal="Prepare delivery",
        deliverable_goal="Reviewable delivery",
        deliverable_kind="file" if file_delivery else "text",
        deliverable_format="markdown" if file_delivery else None,
        deliverable_filename="delivery.md" if file_delivery else "",
        deliverable_requirements=[
            TaskContractItem(id="requirement_summary", description="Contains summary"),
            TaskContractItem(id="requirement_risks", description="Contains risks"),
        ],
        success_criteria=[TaskContractItem(id="criterion_reviewable", description="Reviewable")],
        confirmed_at=now,
        legacy_inferred=True,
    )
    execution = TaskExecution(
        id="execution_1",
        task_id="task_1",
        attempt_no=1,
        trigger_type=ExecutionTriggerType.INITIAL,
        contract_snapshot=contract,
        status=TaskStatus.RUNNING,
        start_node=CurrentNode.DISPATCH_DECISION,
        current_node=CurrentNode.DISPATCH_DECISION,
        created_at=now,
    )
    return Task(
        id="task_1",
        source_type=SourceType.BUSINESS_SYSTEM,
        content="Prepare delivery",
        task_status=TaskStatus.RUNNING,
        current_node=CurrentNode.DISPATCH_DECISION,
        contract=contract,
        context={"artifacts": input_artifacts or []},
        executions=[execution],
        active_execution_id=execution.id,
        created_at=now,
        updated_at=now,
    )


def test_input_attachment_text_is_not_a_structured_output_artifact() -> None:
    task = _task_with_execution(input_artifacts=["input-brief.docx (1200 chars)"])
    service = _artifact_service()

    assert service.current(task) == []
    assert task.artifacts == []


def test_register_task_output_creates_valid_text_artifact_covering_all_requirements() -> None:
    task = _task_with_execution()
    service = _artifact_service()

    artifact = service.register_task_output(task, "Final delivery")

    assert artifact is not None
    assert artifact.kind == ArtifactKind.TEXT
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.content == "Final delivery"
    assert artifact.checksum == "sha256:" + hashlib.sha256(b"Final delivery").hexdigest()
    assert artifact.validation_status == ArtifactValidationStatus.VALID
    assert artifact.deliverable_requirement_ids == []
    assert service.current(task) == [artifact]


def test_register_task_file_output_creates_managed_valid_artifact(
    tmp_path: Path,
) -> None:
    task = _task_with_execution(file_delivery=True)
    service = _artifact_service()
    delivery_path = tmp_path / "delivery.md"
    delivery_path.write_text("Final delivery", encoding="utf-8")
    materialized = MaterializedDeliverable(
        path=delivery_path,
        content="Final delivery",
        media_type="text/markdown",
        delivery_format="markdown",
    )

    artifact = service.register_task_file_output(task, materialized)
    repeated = service.register_task_file_output(task, materialized)

    assert artifact is not None
    assert repeated is artifact
    assert service.current(task) == [artifact]
    assert artifact.kind == ArtifactKind.FILE
    assert artifact.source_type == ArtifactSourceType.TASK_RESULT
    assert artifact.source_id == f"{task.id}:file"
    assert artifact.name == "delivery.md"
    assert artifact.content == "Final delivery"
    assert artifact.uri == delivery_path.resolve().as_uri()
    assert artifact.media_type == "text/markdown"
    assert artifact.checksum == "sha256:" + hashlib.sha256(b"Final delivery").hexdigest()
    assert artifact.validation_status == ArtifactValidationStatus.VALID
    assert artifact.metadata == {
        "managed_final_delivery": True,
        "deliverable_format": "markdown",
        "content_length": len("Final delivery"),
    }


def test_register_task_file_output_does_not_reopen_materialized_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_with_execution(file_delivery=True)
    service = _artifact_service()
    delivery_path = tmp_path / "delivery.md"
    delivery_path.write_text("Final delivery", encoding="utf-8")

    def reject_path_read(_path: Path) -> bytes:
        raise AssertionError("managed artifact registration must use its content snapshot")

    monkeypatch.setattr(Path, "read_bytes", reject_path_read)

    artifact = service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="Final delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )

    assert artifact.checksum == "sha256:" + hashlib.sha256(b"Final delivery").hexdigest()


def test_register_task_file_output_updates_existing_snapshot_in_place(
    tmp_path: Path,
) -> None:
    task = _task_with_execution(file_delivery=True)
    service = _artifact_service()
    delivery_path = tmp_path / "delivery.md"
    delivery_path.write_text("First delivery", encoding="utf-8")
    first = service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="First delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )
    task.artifacts[0] = first.model_copy(
        update={"deliverable_requirement_ids": ["requirement_summary"]}
    )
    original_id = first.id
    original_created_at = first.created_at

    delivery_path.write_text("Updated delivery", encoding="utf-8")
    updated = service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="Updated delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )

    assert updated.id == original_id
    assert updated.created_at == original_created_at
    assert updated.content == "Updated delivery"
    assert updated.checksum == "sha256:" + hashlib.sha256(b"Updated delivery").hexdigest()
    assert updated.deliverable_requirement_ids == ["requirement_summary"]
    assert updated.validation_status == ArtifactValidationStatus.VALID
    assert service.current(task) == [updated]


def test_local_file_uri_to_path_passes_encoded_path_to_url2pathname(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_uri = _file_uri_module()
    converted_path = tmp_path / "converted report.md"
    converted_inputs: list[str] = []

    def convert(path: str) -> str:
        converted_inputs.append(path)
        return str(converted_path)

    monkeypatch.setattr(file_uri, "url2pathname", convert)

    result = file_uri.local_file_uri_to_path(
        "file:///C:/Reports/final%20report.md"
    )

    assert converted_inputs == ["/C:/Reports/final%20report.md"]
    assert result == converted_path


@pytest.mark.parametrize(
    "filename",
    ["literal%2F report.md", "\u4ea4\u4ed8 \u62a5\u544a.md"],
)
def test_local_file_uri_to_path_round_trips_encoded_filename(
    tmp_path: Path,
    filename: str,
) -> None:
    file_uri = _file_uri_module()
    delivery_path = tmp_path / filename
    uri = delivery_path.as_uri()

    if "%2F" in filename:
        assert "%252F" in uri
    assert file_uri.local_file_uri_to_path(uri) == delivery_path


def test_local_file_uri_to_path_preserves_posix_path(tmp_path: Path) -> None:
    file_uri = _file_uri_module()
    delivery_path = tmp_path / "final report.md"

    assert file_uri.local_file_uri_to_path(delivery_path.as_uri()) == delivery_path
    assert file_uri.local_file_uri_to_path("https://example.invalid/report.md") is None
    assert file_uri.local_file_uri_to_path("file://server/share/report.md") is None


def test_artifact_revalidation_uses_shared_file_uri_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_uri = _file_uri_module()
    task = _task_with_execution(file_delivery=True)
    service = _artifact_service()
    delivery_path = tmp_path / "delivery.md"
    delivery_path.write_text("Final delivery", encoding="utf-8")
    artifact = service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="Final delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )
    artifact = service.replace_current(
        task,
        artifact.model_copy(
            update={
                "uri": "file:///C:/managed/delivery.md",
                "validation_status": ArtifactValidationStatus.PENDING,
            }
        ),
    )
    monkeypatch.setattr(
        file_uri,
        "url2pathname",
        lambda _path: str(delivery_path),
    )

    revalidated = service.revalidate(task, artifact)

    assert revalidated.validation_status == ArtifactValidationStatus.VALID


@pytest.mark.parametrize("change", ["delete", "modify", "empty", "snapshot"])
def test_revalidate_file_output_rejects_file_or_snapshot_mismatch(
    tmp_path: Path,
    change: str,
) -> None:
    task = _task_with_execution(file_delivery=True)
    service = _artifact_service()
    delivery_path = tmp_path / "delivery.md"
    delivery_path.write_text("Final delivery", encoding="utf-8")
    artifact = service.register_task_file_output(
        task,
        MaterializedDeliverable(
            path=delivery_path,
            content="Final delivery",
            media_type="text/markdown",
            delivery_format="markdown",
        ),
    )
    assert artifact is not None

    if change == "delete":
        delivery_path.unlink()
    elif change == "modify":
        delivery_path.write_text("Changed delivery", encoding="utf-8")
    elif change == "empty":
        delivery_path.write_bytes(b"")
    else:
        task.artifacts[0] = artifact.model_copy(update={"content": "Different snapshot"})
        artifact = task.artifacts[0]

    revalidated = service.revalidate(task, artifact)

    assert revalidated.validation_status == ArtifactValidationStatus.INVALID
    assert service.current(task)[0].validation_status == ArtifactValidationStatus.INVALID


def test_revalidate_file_write_without_content_snapshot_remains_valid(
    tmp_path: Path,
) -> None:
    task = _task_with_execution()
    service = _artifact_service()
    delivery_path = tmp_path / "tool-output.txt"
    delivery_path.write_text("Tool output", encoding="utf-8")
    artifact = service.register_tool_result(
        task,
        SubTask(id="subtask_tools", title="Use tools", description="Produce outputs"),
        ToolExecutionResult(
            tool_execution_id="tool_file",
            tool_name="write_file",
            tool_type="file_write",
            success=True,
            result=str(delivery_path),
        ),
    )
    assert artifact is not None
    artifact.validation_status = ArtifactValidationStatus.PENDING

    revalidated = service.revalidate(task, artifact)

    assert revalidated.validation_status == ArtifactValidationStatus.VALID


def test_subtask_output_registration_is_idempotent_but_does_not_merge_different_sources() -> None:
    task = _task_with_execution()
    service = _artifact_service()
    first_subtask = SubTask(id="subtask_1", title="Draft", description="Draft delivery")
    second_subtask = SubTask(id="subtask_2", title="Review", description="Review delivery")

    first = service.register_subtask_output(task, first_subtask, "Same content")
    repeated = service.register_subtask_output(task, first_subtask, "Same content")
    second = service.register_subtask_output(task, second_subtask, "Same content")

    assert first is not None
    assert repeated is first
    assert second is not None
    assert second.id != first.id
    assert [artifact.source_id for artifact in service.current(task)] == ["subtask_1", "subtask_2"]


def test_register_tool_results_creates_file_and_receipt_artifacts(tmp_path: Path) -> None:
    task = _task_with_execution()
    service = _artifact_service()
    subtask = SubTask(id="subtask_tools", title="Use tools", description="Produce outputs")
    written_file = tmp_path / "delivery.md"
    written_file.write_text("file delivery", encoding="utf-8")

    file_artifact = service.register_tool_result(
        task,
        subtask,
        ToolExecutionResult(
            tool_name="write_delivery",
            tool_type="file_write",
            arguments={"filename": "delivery.md"},
            success=True,
            result=str(written_file),
        ),
        ordinal=0,
    )
    receipt_artifact = service.register_tool_result(
        task,
        subtask,
        ToolExecutionResult(
            tool_name="crm_query",
            tool_type="mock",
            arguments={"customer_id": "customer_1"},
            success=True,
            result='{"level": "vip"}',
        ),
        ordinal=1,
    )

    assert file_artifact is not None
    assert file_artifact.kind == ArtifactKind.FILE
    assert file_artifact.uri == written_file.resolve().as_uri()
    assert file_artifact.validation_status == ArtifactValidationStatus.VALID
    assert file_artifact.checksum == "sha256:" + hashlib.sha256(b"file delivery").hexdigest()
    assert receipt_artifact is not None
    assert receipt_artifact.kind == ArtifactKind.TOOL_RESULT
    assert receipt_artifact.content == '{"level": "vip"}'
    assert receipt_artifact.validation_status == ArtifactValidationStatus.VALID


@pytest.mark.parametrize(
    "tool_result",
    [
        ToolExecutionResult(tool_name="empty", tool_type="mock", success=True, result=""),
        ToolExecutionResult(tool_name="failed", tool_type="mock", success=False, error="failed"),
    ],
)
def test_empty_or_failed_tool_result_is_not_registered(tool_result: ToolExecutionResult) -> None:
    task = _task_with_execution()
    service = _artifact_service()
    subtask = SubTask(id="subtask_tools", title="Use tools", description="Produce outputs")

    assert service.register_tool_result(task, subtask, tool_result) is None
    assert service.current(task) == []


def test_task_rejects_current_artifact_from_non_active_execution() -> None:
    task = _task_with_execution()
    data = task.model_dump(mode="json")
    data["artifacts"] = [
        core_models.Artifact(
            id="artifact_old",
            task_id=task.id,
            execution_id="execution_old",
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.TASK_RESULT,
            source_id="old_result",
            name="Old result",
            content="Old output",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=utc_now(),
        ).model_dump(mode="json")
    ]

    with pytest.raises(ValidationError, match="current artifacts must belong to the active execution"):
        Task.model_validate(data)


def test_task_rejects_duplicate_artifact_source_keys_inside_execution() -> None:
    task = _task_with_execution()
    data = task.model_dump(mode="json")
    artifacts = [
        core_models.Artifact(
            id=f"artifact_{index}",
            task_id=task.id,
            execution_id=task.active_execution_id,
            kind=ArtifactKind.TEXT,
            source_type=ArtifactSourceType.SUBTASK_OUTPUT,
            source_id="same_subtask",
            name=f"Output {index}",
            content=f"Output {index}",
            validation_status=ArtifactValidationStatus.VALID,
            created_at=utc_now(),
        ).model_dump(mode="json")
        for index in range(2)
    ]
    data["executions"][0]["artifacts"] = artifacts

    with pytest.raises(ValidationError, match="execution artifact source keys must be unique"):
        Task.model_validate(data)


def test_tool_result_registration_uses_execution_id_and_is_idempotent_per_execution() -> None:
    task = _task_with_execution()
    service = _artifact_service()
    subtask = SubTask(id="subtask_tools", title="Use tools", description="Produce outputs")
    first_result = ToolExecutionResult(
        tool_execution_id="tool_execution_1",
        tool_name="crm_query",
        tool_type="mock",
        arguments={"customer_id": "customer_1"},
        success=True,
        result="same result",
    )
    second_result = first_result.model_copy(update={"tool_execution_id": "tool_execution_2"})

    first = service.register_tool_result(task, subtask, first_result, ordinal=0)
    repeated = service.register_tool_result(task, subtask, first_result, ordinal=0)
    second = service.register_tool_result(task, subtask, second_result, ordinal=1)

    assert first is not None and second is not None
    assert repeated is first
    assert second.id != first.id
    assert len(service.current(task)) == 2


def test_tool_result_registration_fallback_uses_ordinal_when_execution_id_is_missing() -> None:
    task = _task_with_execution()
    service = _artifact_service()
    subtask = SubTask(id="subtask_tools", title="Use tools", description="Produce outputs")
    result = ToolExecutionResult(
        tool_name="crm_query",
        tool_type="mock",
        arguments={"customer_id": "customer_1"},
        success=True,
        result="same result",
    )

    first = service.register_tool_result(task, subtask, result, ordinal=0)
    second = service.register_tool_result(task, subtask, result, ordinal=1)

    assert first is not None and second is not None
    assert first.id != second.id


def test_artifact_registration_after_execution_is_sealed_is_rejected_atomically() -> None:
    task = _task_with_execution()
    task.executions[0].finished_at = utc_now()
    before = task.model_dump(mode="json")
    service = _artifact_service()
    closed_error = getattr(__import__("app.services.artifact_service", fromlist=["ArtifactRegistrationClosedError"]), "ArtifactRegistrationClosedError", None)
    assert closed_error is not None

    with pytest.raises(closed_error, match="closed"):
        service.register_task_output(task, "Late output")

    assert task.model_dump(mode="json") == before


def test_artifact_replacement_after_execution_is_sealed_is_rejected_atomically() -> None:
    task = _task_with_execution()
    service = _artifact_service()
    artifact = service.register_task_output(task, "Final delivery")
    assert artifact is not None
    task.executions[0].finished_at = utc_now()
    before = task.model_dump(mode="json")

    with pytest.raises(
        __import__(
            "app.services.artifact_service",
            fromlist=["ArtifactRegistrationClosedError"],
        ).ArtifactRegistrationClosedError,
        match="closed",
    ):
        service.replace_current(
            task,
            artifact.model_copy(
                update={
                    "validation_status": ArtifactValidationStatus.INVALID,
                    "validation_reason": "Changed after seal",
                }
            ),
        )

    assert task.model_dump(mode="json") == before
