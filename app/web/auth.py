from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.consultant_access import consultant_allowed_path, is_consultant_user
from app.services.rbac import Permission, has_permission


def current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.active:
        request.session.clear()
        return None
    return user


def require_user(request: Request, user: Optional[User] = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not user:
        raise LoginRedirect(request.url.path)
    if is_consultant_user(user) and not consultant_allowed_path(request.url.path, user, db):
        raise PermissionDenied("This consultant section is locked until the required readiness step is complete.")
    return user


def require_manager(user: User = Depends(require_user)) -> User:
    if not has_permission(user, Permission.ASSIGN_PURSUITS):
        raise PermissionDenied("Manager access is required.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise PermissionDenied("Admin access is required.")
    return user


def require_permission(permission: Permission | str):
    def dependency(user: User = Depends(require_user)) -> User:
        if not has_permission(user, permission):
            raise PermissionDenied(f"Missing permission: {permission}")
        return user

    return dependency


class LoginRedirect(Exception):
    def __init__(self, next_url: str) -> None:
        self.next_url = next_url


class PermissionDenied(Exception):
    def __init__(self, message: str = "Permission denied") -> None:
        self.message = message


def login_redirect_response(next_url: str = "/dashboard") -> RedirectResponse:
    return RedirectResponse(f"/login?next={next_url}", status_code=303)


def _is_consultant_user(user: User | object | None) -> bool:
    return is_consultant_user(user)
