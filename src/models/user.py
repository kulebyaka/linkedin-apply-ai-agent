"""Pydantic models for user accounts and authentication.

This module contains models for:
- User entity with profile, master CV, and search preferences
- Authentication request/response models (magic link flow)
- User update models for settings API
- User search preferences (mirrors LinkedInSearchParams)
"""

import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class UserSearchPreferences(BaseModel):
    """User's LinkedIn search preferences.

    Mirrors LinkedInSearchParams fields for per-user search configuration.
    """

    keywords: str = ""
    location: str = ""
    remote_filter: str | None = None  # "remote", "on-site", "hybrid"
    date_posted: str | None = None  # "24h", "week", "month"
    experience_level: list[str] | None = None  # "entry", "associate", "mid-senior", etc.
    job_type: list[str] | None = None  # "full-time", "part-time", "contract", etc.
    easy_apply_only: bool = False
    max_jobs: int = Field(default=50, ge=1, le=500)


class User(BaseModel):
    """User entity stored in the database."""

    id: str
    email: str
    display_name: str
    master_cv_json: dict | None = None
    search_preferences: UserSearchPreferences | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


# =============================================================================
# Authentication Models
# =============================================================================


class LoginRequest(BaseModel):
    """Request to initiate magic link authentication."""

    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address")
        return v


class LoginResponse(BaseModel):
    """Response after magic link is sent."""

    message: str


class AuthResponse(BaseModel):
    """Response after successful authentication."""

    user: User
    message: str


# =============================================================================
# User Update Models
# =============================================================================


class UserUpdateRequest(BaseModel):
    """Request to update user profile fields.

    All fields are optional - only provided fields are updated.
    """

    display_name: str | None = None
    master_cv_json: dict | None = None
    search_preferences: UserSearchPreferences | None = None
