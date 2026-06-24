"""Authentication endpoints: magic link login, verify, dev-login, me, logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from src.api.deps import CurrentUser, get_ctx
from src.models.user import AuthResponse, LoginRequest, LoginResponse, User

logger = logging.getLogger(__name__)
router = APIRouter()


class ExtensionTokenResponse(BaseModel):
    """A short-lived bearer token the Chrome extension uses for the WS bridge."""

    token: str


@router.post("/api/auth/login", response_model=LoginResponse)
async def auth_login(body: LoginRequest, request: Request) -> LoginResponse:
    """Request a magic link login email."""
    ctx = get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")

    try:
        await ctx.auth_service.send_magic_link(body.email)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None

    return LoginResponse(message="Check your email for a magic link")


@router.get("/api/auth/verify", response_model=AuthResponse)
async def auth_verify(
    request: Request,
    response: Response,
    token: str = Query(...),
) -> AuthResponse:
    """Verify a magic link token and set auth cookie."""
    ctx = get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")

    try:
        user = await ctx.auth_service.verify_token(token)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    jwt_token = ctx.auth_service.create_jwt(user.id, user.email)

    response.set_cookie(
        key="auth_token",
        value=jwt_token,
        httponly=True,
        max_age=ctx.settings.jwt_expiry_days * 86400,
        samesite="lax",
        secure=ctx.settings.app_url.startswith("https://"),
        path="/",
    )

    return AuthResponse(user=user, message="Logged in successfully")


@router.post("/api/auth/dev-login", response_model=AuthResponse)
async def auth_dev_login(request: Request, response: Response) -> AuthResponse:
    """Local-development auth bypass — mint a JWT for the configured dev user.

    Gated by ``DEV_AUTH_BYPASS=true``. Returns 404 when disabled so the
    route looks identical to a non-existent endpoint in production.
    """
    ctx = get_ctx(request)
    if not ctx.settings.dev_auth_bypass:
        raise HTTPException(404, "Not Found")
    if ctx.auth_service is None or ctx.user_repository is None:
        raise HTTPException(500, "Auth service not initialized")

    email = ctx.settings.dev_auth_email
    user = await ctx.user_repository.get_by_email(email)
    if user is None:
        user = await ctx.user_repository.create_user(email)
        logger.info("dev-login: auto-created user %s", email)

    jwt_token = ctx.auth_service.create_jwt(user.id, user.email)
    response.set_cookie(
        key="auth_token",
        value=jwt_token,
        httponly=True,
        max_age=ctx.settings.jwt_expiry_days * 86400,
        samesite="lax",
        secure=ctx.settings.app_url.startswith("https://"),
        path="/",
    )
    return AuthResponse(user=user, message="dev-login: logged in as " + email)


@router.get("/api/auth/me", response_model=User)
async def auth_me(user: CurrentUser) -> User:
    """Get the currently authenticated user."""
    return user


@router.get("/api/auth/extension-token", response_model=ExtensionTokenResponse)
async def auth_extension_token(request: Request, user: CurrentUser) -> ExtensionTokenResponse:
    """Mint a JWT for the Chrome extension's WebSocket handshake.

    The app's session JWT lives in an httpOnly cookie that JS can't read, so
    the `/extension-auth` page calls this (with credentials) to obtain a token
    string it can hand to the extension via ``chrome.runtime.sendMessage``.
    The token is identical in shape/claims to the session cookie and is
    validated by ``WsRelay`` via ``auth_service.decode_jwt``.
    """
    ctx = get_ctx(request)
    if ctx.auth_service is None:
        raise HTTPException(500, "Auth service not initialized")
    token = ctx.auth_service.create_jwt(user.id, user.email)
    return ExtensionTokenResponse(token=token)


@router.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Clear auth cookie to log out."""
    ctx = get_ctx(request)
    response.delete_cookie(
        key="auth_token",
        path="/",
        httponly=True,
        samesite="lax",
        secure=ctx.settings.app_url.startswith("https://"),
    )
    return {"message": "Logged out"}
