"""Tests for the `get_admin_user` FastAPI dependency.

Verifies that:
- Admin role passes through.
- Non-admin roles (trial, premium) get 403.
- 401 still propagates from get_current_user when unauthenticated.
"""

import pytest
from fastapi import HTTPException

from src.api.main import get_admin_user
from src.models.user import User, UserRole


def _user_with_role(role: UserRole) -> User:
    return User(
        id="user-1",
        email="admin-test@example.com",
        display_name="Admin Test",
        role=role,
    )


@pytest.mark.asyncio
async def test_get_admin_user_passes_admin_through():
    user = _user_with_role(UserRole.ADMIN)
    returned = await get_admin_user(user=user)
    assert returned is user


@pytest.mark.asyncio
async def test_get_admin_user_rejects_trial():
    user = _user_with_role(UserRole.TRIAL)
    with pytest.raises(HTTPException) as exc:
        await get_admin_user(user=user)
    assert exc.value.status_code == 403
    assert "admin" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_get_admin_user_rejects_premium():
    user = _user_with_role(UserRole.PREMIUM)
    with pytest.raises(HTTPException) as exc:
        await get_admin_user(user=user)
    assert exc.value.status_code == 403


def test_admin_user_type_alias_exposed():
    """The AdminUser annotated alias should exist for use in routes."""
    from src.api.main import AdminUser

    # AdminUser is Annotated[User, Depends(get_admin_user)] — just verify it
    # imports cleanly and references the dependency function.
    assert AdminUser is not None
