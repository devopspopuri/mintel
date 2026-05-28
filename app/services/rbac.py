from __future__ import annotations

from enum import Enum
from typing import Iterable

from app.models.user import User, UserRole


class Permission(str, Enum):
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_USCIS = "view_uscis"
    IMPORT_USCIS = "import_uscis"
    VIEW_REPORTS = "view_reports"
    VIEW_COMPANIES = "view_companies"
    MANAGE_COMPANIES = "manage_companies"
    VIEW_PURSUITS = "view_pursuits"
    ASSIGN_PURSUITS = "assign_pursuits"
    MANAGE_PURSUIT_WORKSPACE = "manage_pursuit_workspace"
    VIEW_OPERATIONS = "view_operations"
    MANAGE_OPERATIONS = "manage_operations"
    VIEW_REGIONS = "view_regions"
    MANAGE_REGIONS = "manage_regions"
    VIEW_API_DOCS = "view_api_docs"
    MANAGE_STAFF = "manage_staff"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    UserRole.ADMIN.value: set(Permission),
    UserRole.MANAGER.value: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_USCIS,
        Permission.IMPORT_USCIS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_COMPANIES,
        Permission.MANAGE_COMPANIES,
        Permission.VIEW_PURSUITS,
        Permission.ASSIGN_PURSUITS,
        Permission.MANAGE_PURSUIT_WORKSPACE,
        Permission.VIEW_OPERATIONS,
        Permission.MANAGE_OPERATIONS,
        Permission.VIEW_REGIONS,
        Permission.MANAGE_REGIONS,
        Permission.VIEW_API_DOCS,
    },
    UserRole.REGIONAL_STAFF.value: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_USCIS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_COMPANIES,
        Permission.VIEW_PURSUITS,
        Permission.MANAGE_PURSUIT_WORKSPACE,
        Permission.VIEW_OPERATIONS,
        Permission.VIEW_REGIONS,
        Permission.VIEW_API_DOCS,
    },
    UserRole.VIEWER.value: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_USCIS,
        Permission.VIEW_REPORTS,
        Permission.VIEW_COMPANIES,
        Permission.VIEW_PURSUITS,
        Permission.VIEW_OPERATIONS,
        Permission.VIEW_REGIONS,
        Permission.VIEW_API_DOCS,
    },
    UserRole.CONSULTANT.value: set(),
}


def role_value(user: User | object | None) -> str:
    role = getattr(user, "role", "")
    return getattr(role, "value", role) or ""


def permissions_for_role(role: str | UserRole) -> set[Permission]:
    value = getattr(role, "value", role) or ""
    return set(ROLE_PERMISSIONS.get(str(value), set()))


def permissions_for_user(user: User | object | None) -> set[Permission]:
    return permissions_for_role(role_value(user))


def has_permission(user: User | object | None, permission: Permission | str) -> bool:
    try:
        required = permission if isinstance(permission, Permission) else Permission(permission)
    except ValueError:
        return False
    return required in permissions_for_user(user)


def has_any_permission(user: User | object | None, permissions: Iterable[Permission | str]) -> bool:
    return any(has_permission(user, permission) for permission in permissions)


def can_manage(user: User | object | None) -> bool:
    return has_any_permission(
        user,
        [
            Permission.MANAGE_COMPANIES,
            Permission.ASSIGN_PURSUITS,
            Permission.MANAGE_OPERATIONS,
            Permission.MANAGE_REGIONS,
        ],
    )


def is_admin(user: User | object | None) -> bool:
    return role_value(user) == UserRole.ADMIN.value
