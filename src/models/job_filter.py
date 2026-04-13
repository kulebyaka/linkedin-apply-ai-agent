"""Pydantic models for LLM-based job filtering.

This module contains models for:
- FilterResult: LLM evaluation output (score, red flags, disqualification)
- UserFilterPreferences: Per-user filter configuration (prompts, thresholds)
"""

from pydantic import BaseModel, Field, field_validator


class FilterResult(BaseModel):
    """Result of LLM job evaluation.

    Returned by JobFilter.evaluate_job() and stored on JobRecord.
    """

    score: int = Field(..., ge=0, le=100, description="Overall suitability score 0-100")
    red_flags: list[str] = Field(
        default_factory=list,
        description="List of detected red flags or concerns",
    )
    disqualified: bool = Field(
        False,
        description="True if a hard disqualifier was found",
    )
    disqualifier_reason: str | None = Field(
        None,
        description="Reason for disqualification (if disqualified=True)",
    )
    reasoning: str = Field(
        ...,
        description="LLM's reasoning for the score and flags",
    )


class UserFilterPreferences(BaseModel):
    """Per-user filter configuration.

    Stored as JSON on UserTable.filter_preferences.
    """

    natural_language_prefs: str = Field(
        "",
        description="User's natural language description of what they don't want (textarea 1)",
    )
    custom_prompt: str | None = Field(
        None,
        description="Custom filter prompt generated/edited by user (textarea 2). None means use default.",
    )
    reject_threshold: int = Field(
        30,
        ge=0,
        le=100,
        description="Jobs scoring below this are auto-rejected",
    )
    warning_threshold: int = Field(
        70,
        ge=0,
        le=100,
        description="Jobs scoring between reject and this show a warning badge",
    )
    enabled: bool = Field(
        True,
        description="Whether filtering is active for this user",
    )

    @field_validator("warning_threshold")
    @classmethod
    def warning_gte_reject(cls, v: int, info) -> int:
        reject = info.data.get("reject_threshold", 30)
        if v < reject:
            raise ValueError(
                f"warning_threshold ({v}) must be >= reject_threshold ({reject})"
            )
        return v


class GeneratePromptRequest(BaseModel):
    """Request body for generating a filter prompt from natural language preferences."""

    natural_language_prefs: str = Field(
        ...,
        description="User's natural language description of what they want / don't want in a job",
    )
