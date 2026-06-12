"""Pydantic models for LLM-based job filtering.

This module contains models for:
- FilterResult: LLM evaluation output (score, red flags, disqualification)
- UserFilterPreferences: Per-user filter configuration (prompts, thresholds)
- RefinementProposal: A pending auto-refinement proposal for the filter prompt
- Auto-learned block helpers: marker-delimited region inside ``custom_prompt``
  that the refiner exclusively owns (the user's hand-written text is never
  touched).
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator

# Delimiters for the refiner-owned region inside ``custom_prompt``. Everything
# between these markers is replaced by the refiner on accept; everything outside
# is the user's hand-written prompt and is preserved verbatim.
AUTO_LEARNED_BEGIN = "<!-- BEGIN auto-learned -->"
AUTO_LEARNED_END = "<!-- END auto-learned -->"


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
        max_length=5000,
        description="User's natural language description of what they don't want (textarea 1)",
    )
    custom_prompt: str | None = Field(
        None,
        max_length=20000,
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
    auto_refine_enabled: bool = Field(
        False,
        description=(
            "Opt-in: periodically analyze declines/overrides and PROPOSE an "
            "updated auto-learned criteria block for review. Default OFF."
        ),
    )

    @model_validator(mode="after")
    def warning_gte_reject(self) -> "UserFilterPreferences":
        if self.warning_threshold < self.reject_threshold:
            raise ValueError(
                f"warning_threshold ({self.warning_threshold}) must be >= reject_threshold ({self.reject_threshold})"
            )
        return self


class GeneratePromptRequest(BaseModel):
    """Request body for generating a filter prompt from natural language preferences."""

    natural_language_prefs: str = Field(
        ...,
        max_length=5000,
        description="User's natural language description of what they want / don't want in a job",
    )


class RefinementProposal(BaseModel):
    """A pending, reviewable proposal to update the auto-learned criteria block.

    Stored as JSON on the user record (one pending proposal per user; a new
    cycle supersedes any prior unacknowledged one). The proposal is generated
    from decline/override signals and only ever PROPOSED — the user accepts or
    rejects it explicitly.
    """

    proposed_learned_block: str = Field(
        ...,
        description="The full proposed '## Auto-learned criteria' block (no markers).",
    )
    rationale: str = Field(
        ...,
        description="Plain-language explanation of the change, citing the signals.",
    )
    signal_job_ids: list[str] = Field(
        default_factory=list,
        description="Job ids whose decline/override signals fed this proposal.",
    )
    decline_count: int = Field(0, description="How many decline signals were considered.")
    override_count: int = Field(0, description="How many override signals were considered.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="When the proposal was generated.",
    )


def extract_learned_block(custom_prompt: str | None) -> str | None:
    """Return the content between the auto-learned markers, or None if absent.

    The returned text excludes the marker lines themselves. Leading/trailing
    whitespace is stripped.
    """
    if not custom_prompt:
        return None
    start = custom_prompt.find(AUTO_LEARNED_BEGIN)
    end = custom_prompt.find(AUTO_LEARNED_END)
    if start == -1 or end == -1 or end < start:
        return None
    inner = custom_prompt[start + len(AUTO_LEARNED_BEGIN) : end]
    return inner.strip()


def apply_learned_block(custom_prompt: str | None, learned_block: str) -> str:
    """Return ``custom_prompt`` with the auto-learned region set to ``learned_block``.

    - If both markers are present, only the delimited region is replaced; the
      user's hand-written text outside the markers is preserved verbatim.
    - If the markers are absent, a fresh delimited block is appended.

    The result always contains exactly one well-formed marker pair.
    """
    block = learned_block.strip()
    # Defensively strip any markers the proposed block may already contain so we
    # never emit a nested/duplicated marker pair, which would corrupt later
    # extract/replace (find() locates the first marker only).
    block = block.replace(AUTO_LEARNED_BEGIN, "").replace(AUTO_LEARNED_END, "").strip()
    delimited = f"{AUTO_LEARNED_BEGIN}\n{block}\n{AUTO_LEARNED_END}"

    base = custom_prompt or ""
    start = base.find(AUTO_LEARNED_BEGIN)
    end = base.find(AUTO_LEARNED_END)

    if start != -1 and end != -1 and end >= start:
        before = base[:start].rstrip()
        after = base[end + len(AUTO_LEARNED_END) :].lstrip()
        parts = [p for p in (before, delimited, after) if p]
        return "\n\n".join(parts)

    # Markers absent (or malformed) — append a fresh block.
    head = base.rstrip()
    if head:
        return f"{head}\n\n{delimited}"
    return delimited
