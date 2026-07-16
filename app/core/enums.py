from enum import StrEnum


class SourceType(StrEnum):
    HUMAN = "human"
    BUSINESS_SYSTEM = "business_system"
    AGENT = "agent"


class TaskStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
