"""Request-local identity used by filesystem task persistence."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional


current_user: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "current_user",
    default=None,
)


def get_current_user() -> Optional[dict[str, Any]]:
    """Return the authenticated user for the current request or background task."""
    return current_user.get()


def get_current_user_id() -> str:
    """Return the current user's stable string identifier."""
    user = get_current_user()
    return str(user.get("id", "")) if user else ""


def current_user_is_admin() -> bool:
    """Return whether the request identity can administer all tasks."""
    user = get_current_user()
    return bool(user and user.get("role") in {"admin", "superadmin"})
