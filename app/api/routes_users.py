from fastapi import APIRouter, HTTPException, Request, status

from app.api.auth import current_user, require_admin
from app.core.models import User, UserCreate, UserOption, UserUpdate

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/current", response_model=User)
def get_current_user(request: Request) -> User:
    return current_user(request)


@router.get("/assignable", response_model=list[UserOption])
def list_assignable_users(request: Request) -> list[UserOption]:
    current_user(request)
    users = request.app.state.user_registry.list_users()
    return [
        UserOption(id=user.id, name=user.name, role=user.role)
        for user in users
        if user.status == "active"
    ]


@router.get("", response_model=list[User])
def list_users(request: Request) -> list[User]:
    user = current_user(request)
    require_admin(user)
    return request.app.state.user_registry.list_users()


@router.post("", response_model=User, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, request: Request) -> User:
    user = current_user(request)
    require_admin(user)
    return request.app.state.user_registry.create_user(payload)


@router.put("/{user_id}", response_model=User)
def update_user(user_id: str, payload: UserUpdate, request: Request) -> User:
    user = current_user(request)
    require_admin(user)
    updated = request.app.state.user_registry.update_user(user_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, request: Request) -> None:
    user = current_user(request)
    require_admin(user)
    deleted = request.app.state.user_registry.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
