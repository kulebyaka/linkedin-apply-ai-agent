"""Shared FastAPI dependencies and helpers for API routers.

Extracted from src/api/main.py so route modules can depend on a stable
import surface without pulling in the entire app instance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request

from src.context import AppContext
from src.models.user import User, UserRole
from src.services.jobs.hitl_processor import HITLProcessor
from src.services.jobs.job_orchestrator import JobOrchestrator


def get_ctx(request: Request) -> AppContext:
    """Retrieve the AppContext stored on app.state."""
    return request.app.state.ctx


def normalize_query_datetime(value: datetime | None) -> datetime | None:
    """FastAPI parses bare YYYY-MM-DD as naive; JobRecord.created_at is UTC-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_current_user(
    request: Request,
    auth_token: str | None = Cookie(default=None),
) -> User:
    """FastAPI dependency: require authenticated user.

    Reads JWT from the auth_token cookie, decodes it, and looks up the
    user in the repository. Raises 401 if not authenticated.
    """
    if not auth_token:
        raise HTTPException(401, "Not authenticated")

    ctx = get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")
    if ctx.user_repository is None:
        raise HTTPException(500, "User repository not initialized")

    try:
        claims = ctx.auth_service.decode_jwt(auth_token)
    except ValueError:
        raise HTTPException(401, "Invalid or expired token") from None

    user = await ctx.user_repository.get_by_id(claims["user_id"])
    if user is None:
        raise HTTPException(401, "User not found")

    return user


async def get_optional_user(
    request: Request,
    auth_token: str | None = Cookie(default=None),
) -> User | None:
    """FastAPI dependency: optionally authenticated user.

    Returns User if valid auth cookie present, None otherwise. Does not
    raise 401 — for public endpoints that behave differently when
    authenticated.
    """
    if not auth_token:
        return None

    ctx = get_ctx(request)
    if ctx.auth_service is None or ctx.user_repository is None:
        return None

    try:
        claims = ctx.auth_service.decode_jwt(auth_token)
    except ValueError:
        return None

    return await ctx.user_repository.get_by_id(claims["user_id"])


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_admin_user(user: CurrentUser) -> User:
    """FastAPI dependency: require role == admin (depends on CurrentUser)."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Admin role required")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def get_orchestrator(request: Request) -> JobOrchestrator:
    """Retrieve the JobOrchestrator from the AppContext."""
    ctx = get_ctx(request)
    if ctx.orchestrator is None:
        raise HTTPException(503, "JobOrchestrator not initialized")
    return ctx.orchestrator


def get_hitl_processor(request: Request) -> HITLProcessor:
    """Retrieve the HITLProcessor from the AppContext."""
    ctx = get_ctx(request)
    if ctx.hitl_processor is None:
        raise HTTPException(503, "HITLProcessor not initialized")
    return ctx.hitl_processor
