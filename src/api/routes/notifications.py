"""Persistent notification endpoints: list, unread count, mark read/all-read."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from src.api.deps import CurrentUser, get_ctx
from src.models.notification import Notification

logger = logging.getLogger(__name__)
router = APIRouter()


def _repo(request: Request):
    ctx = get_ctx(request)
    if ctx.notification_repository is None:
        raise HTTPException(503, "Notification repository not initialized")
    return ctx.notification_repository


@router.get("/api/notifications", response_model=list[Notification])
async def list_notifications(
    request: Request,
    user: CurrentUser,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Notification]:
    """List the current user's notifications, newest first."""
    try:
        return await _repo(request).list_for_user(
            user.id, unread_only=unread_only, limit=limit, offset=offset
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list notifications: {e}", exc_info=True)
        raise HTTPException(500, "Failed to list notifications") from None


@router.get("/api/notifications/unread-count")
async def unread_count(request: Request, user: CurrentUser) -> dict[str, int]:
    """Return the unread notification count for the bell badge."""
    try:
        return {"count": await _repo(request).unread_count(user.id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get unread count: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get unread count") from None


@router.put("/api/notifications/{notification_id}/read")
async def mark_read(
    notification_id: str, request: Request, user: CurrentUser
) -> dict:
    """Mark one notification read."""
    try:
        ok = await _repo(request).mark_read(notification_id, user.id)
        if not ok:
            raise HTTPException(404, "Notification not found")
        return {"id": notification_id, "read": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to mark notification read: {e}", exc_info=True)
        raise HTTPException(500, "Failed to mark notification read") from None


@router.put("/api/notifications/read-all")
async def mark_all_read(request: Request, user: CurrentUser) -> dict:
    """Mark all of the user's notifications read."""
    try:
        updated = await _repo(request).mark_all_read(user.id)
        return {"updated": updated}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to mark all notifications read: {e}", exc_info=True)
        raise HTTPException(500, "Failed to mark all read") from None
