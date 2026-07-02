"""Pydantic models for user accounts and authentication.

This module contains models for:
- User entity with profile, master CV, and search preferences
- Authentication request/response models (magic link flow)
- User update models for settings API
- User search preferences (mirrors LinkedInSearchParams)
"""

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .job_filter import UserFilterPreferences


class UserRole(str, Enum):
    """User role / access tier.

    Used to gate admin endpoints and (later) feature flags.
    """

    TRIAL = "trial"
    PREMIUM = "premium"
    ADMIN = "admin"


class ModelChoice(BaseModel):
    """A user's chosen LLM model for a specific operation."""

    provider: Literal["openai", "deepseek", "grok", "anthropic"]
    model: str = Field(min_length=1)


class UserModelPreferences(BaseModel):
    """Per-operation LLM model choices for a user."""

    cv_generation: ModelChoice | None = None
    job_filtering: ModelChoice | None = None
    filter_prompt_generation: ModelChoice | None = None


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


class ApplyProfile(BaseModel):
    """Structured answers reused to fill LinkedIn Easy Apply screening fields.

    Every field is optional. Absence means "unknown" — the deterministic field
    classifier treats a required-but-missing value as an abort signal
    (``manual_required``) rather than guessing. Captured once in Settings and
    reused across applications.
    """

    phone_country_code: str | None = None
    years_experience: int | None = None
    expected_salary: str | None = None
    needs_visa_sponsorship: bool | None = None
    legally_authorized: bool | None = None
    willing_to_relocate: bool | None = None
    drivers_license: bool | None = None

    def is_complete_for(self, required_kinds: set[str]) -> bool:
        """Return True iff every required field "kind" has a known value.

        ``required_kinds`` are the classifier field-kind names (matching this
        model's attribute names). A kind whose value is ``None`` — or any kind
        with no corresponding attribute — makes the profile incomplete, which
        the apply workflow maps to an abort (``manual_required``).
        """
        for kind in required_kinds:
            if getattr(self, kind, None) is None:
                return False
        return True


class User(BaseModel):
    """User entity stored in the database."""

    id: str
    email: str
    display_name: str
    role: UserRole = UserRole.TRIAL
    master_cv_json: dict | None = None
    search_preferences: UserSearchPreferences | None = None
    filter_preferences: UserFilterPreferences | None = None
    model_preferences: UserModelPreferences | None = None
    apply_profile: ApplyProfile | None = None
    auto_apply: bool = False
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

    display_name: str | None = Field(default=None, min_length=1)
    master_cv_json: dict | None = None
    search_preferences: UserSearchPreferences | None = None
    filter_preferences: UserFilterPreferences | None = None
    model_preferences: UserModelPreferences | None = None
    apply_profile: ApplyProfile | None = None
    auto_apply: bool | None = None
