from fastapi import HTTPException, Request

from app.core.enums import UserRole
from app.core.models import Task, User

CURRENT_USER_HEADER = "X-User-Id"
DEFAULT_USER_ID = "root"


def current_user(request: Request) -> User:
    user_id = request.headers.get(CURRENT_USER_HEADER) or DEFAULT_USER_ID
    user = request.app.state.user_registry.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Current user not found")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Current user is disabled")
    return user


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin permission required")


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def ensure_task_access(user: User, task: Task) -> None:
    if is_admin(user):
        return
    if task.created_by_user_id == user.id:
        return
    raise HTTPException(status_code=403, detail="Task permission denied")


def filter_tasks_for_user(user: User, tasks: list[Task]) -> list[Task]:
    if is_admin(user):
        return tasks
    return [task for task in tasks if task.created_by_user_id == user.id]
