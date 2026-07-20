from enum import StrEnum


class SourceType(StrEnum):
    HUMAN = "human"
    BUSINESS_SYSTEM = "business_system"
    AGENT = "agent"


class TaskStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class CriterionResultStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


class ArtifactKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    TOOL_RESULT = "tool_result"


class ArtifactSourceType(StrEnum):
    TASK_RESULT = "task_result"
    SUBTASK_OUTPUT = "subtask_output"
    TOOL_RESULT = "tool_result"


class ArtifactValidationStatus(StrEnum):
    VALID = "valid"
    PENDING = "pending"
    INVALID = "invalid"


class TaskType(StrEnum):
    AUTO_PLANNING = "auto_planning"
    MANUAL_ORCHESTRATION = "manual_orchestration"


class ExecutionTriggerType(StrEnum):
    INITIAL = "initial"
    RERUN = "rerun"


class CurrentNode(StrEnum):
    INTENT_RECOGNITION = "intent_recognition"
    HUMAN_CONFIRMATION = "human_confirmation"
    WAITING_DEPENDENCIES = "waiting_dependencies"
    DISPATCH_DECISION = "dispatch_decision"
    SUBTASK_EXECUTION = "subtask_execution"
    CONTEXT_UPDATE = "context_update"
    AGENT_EXECUTION = "agent_execution"
    HUMAN_EXECUTION = "human_execution"
    COMPLETION_JUDGE = "completion_judge"
    HUMAN_INTERVENTION = "human_intervention"


class ResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"
