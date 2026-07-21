"""LLM-based job filtering service.

Evaluates job postings for hidden disqualifiers (fake remote, buried hard
requirements, misleading titles) and scores overall suitability. Uses
configurable per-user prompts and two-threshold routing (reject / warn / pass).

Mirrors the CVComposer pattern: sync LLM calls wrapped in asyncio.to_thread
by the caller, PromptLoader for external prompt templates, and
BaseLLMClient.generate_json with schema enforcement.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.prompt_spec import PromptSpec
from src.llm.provider import BaseLLMClient
from src.models.job_filter import FilterResult, UserFilterPreferences
from src.services.cv.cv_prompts import PromptLoader

logger = logging.getLogger(__name__)

# JSON schema derived from Pydantic model for LLM structured output
FILTER_RESULT_SCHEMA = FilterResult.model_json_schema()

# Default prompts directory
DEFAULT_PROMPTS_DIR = "prompts/job_filter"


class JobFilterError(Exception):
    """Raised when job filtering fails."""


class JobFilter:
    """LLM-powered job suitability evaluator.

    Evaluates job postings against user preferences and detects hidden
    disqualifiers. Returns a FilterResult with score, red flags, and
    disqualification status.
    """

    TEMPERATURE = 0.2  # Low temperature for consistent evaluations

    def __init__(
        self,
        llm_client: BaseLLMClient,
        prompts_dir: str | None = None,
    ):
        self.llm = llm_client
        self.prompts = PromptLoader(prompts_dir or DEFAULT_PROMPTS_DIR)

    def evaluate_job(
        self,
        job_posting: dict[str, Any],
        user_filter_prefs: UserFilterPreferences | None = None,
        user_id: str = "",
    ) -> FilterResult:
        """Evaluate a job posting for suitability.

        Uses the user's custom prompt if set, otherwise falls back to the
        default filter prompt template.

        Args:
            job_posting: Normalized job data with keys like title, company,
                         location, description.
            user_filter_prefs: Optional per-user filter preferences containing
                               custom prompt and natural language prefs.
            user_id: User identifier used to scope the prompt cache key so
                     per-user ``custom_prompt`` differences do not contend.

        Returns:
            FilterResult with score, red flags, and disqualification info.

        Raises:
            JobFilterError: If LLM call or result parsing fails.
        """
        job_title = job_posting.get("title", "N/A")
        company = job_posting.get("company", "N/A")
        logger.info(f"Evaluating job: {job_title} at {company}")

        spec = self._build_evaluation_spec(job_posting, user_filter_prefs, user_id)

        try:
            raw_result = self.llm.generate_json(
                spec,
                schema=FILTER_RESULT_SCHEMA,
                temperature=self.TEMPERATURE,
                validator=lambda data: FilterResult(**data),
            )
        except Exception as e:
            logger.error(f"LLM evaluation failed for {job_title} at {company}: {e}")
            raise JobFilterError(f"Job evaluation failed: {e}") from e

        try:
            result = FilterResult(**raw_result)
        except Exception as e:
            logger.error(f"FilterResult validation failed: {e}")
            raise JobFilterError(f"Invalid filter result structure: {e}") from e

        logger.info(
            f"Job evaluation complete: score={result.score}, "
            f"disqualified={result.disqualified}, "
            f"red_flags={len(result.red_flags)}"
        )
        return result

    def generate_prompt_from_preferences(
        self,
        natural_language_prefs: str,
        user_id: str = "",
    ) -> str:
        """Generate a structured filter prompt from natural language preferences.

        Calls the LLM with a meta-prompt that converts the user's free-text
        description of their preferences into a concrete evaluation prompt.

        Args:
            natural_language_prefs: User's natural language description of
                                    what they want / don't want.
            user_id: User identifier for the prompt cache key.

        Returns:
            Generated prompt string suitable for textarea 2 in the UI.

        Raises:
            JobFilterError: If LLM call fails.
        """
        logger.info("Generating filter prompt from user preferences")

        spec = self.prompts.load_spec(
            "generate_prompt_from_prefs",
            cache_key=f"filter_prompt_gen:{user_id}" if user_id else "",
            user_vars={"natural_language_prefs": natural_language_prefs},
        )

        try:
            generated = self.llm.generate(spec, temperature=0.4)
        except Exception as e:
            logger.error(f"Prompt generation failed: {e}")
            raise JobFilterError(f"Prompt generation failed: {e}") from e

        generated = generated.strip()
        logger.info(f"Generated filter prompt ({len(generated)} chars)")
        return generated

    # JSON schema for the refinement LLM call.
    _REFINEMENT_SCHEMA = {
        "type": "object",
        "properties": {
            "proposed_learned_block": {
                "type": "string",
                "description": "Full '## Auto-learned criteria' markdown block.",
            },
            "rationale": {
                "type": "string",
                "description": "Plain-language explanation citing the signals.",
            },
        },
        "required": ["proposed_learned_block", "rationale"],
        "additionalProperties": False,
    }

    def generate_refinement(
        self,
        current_learned_block: str,
        decline_signals: list[str],
        override_signals: list[str],
        user_id: str = "",
    ) -> dict[str, str]:
        """Propose an updated auto-learned criteria block from user signals.

        Args:
            current_learned_block: The existing auto-learned block (may be "").
            decline_signals: False-positive reasons (filter passed, user declined).
            override_signals: False-negative reasons (filter rejected, user forced through).
            user_id: User identifier for the prompt cache key.

        Returns:
            Dict with ``proposed_learned_block`` and ``rationale``.

        Raises:
            JobFilterError: If the LLM call fails or returns an invalid block.
        """
        logger.info(
            "Generating filter refinement: %d decline + %d override signals",
            len(decline_signals),
            len(override_signals),
        )

        def _fmt(items: list[str]) -> str:
            return "\n".join(f"- {s}" for s in items) if items else "(none)"

        spec = self.prompts.load_spec(
            "refine_filter_prompt",
            cache_key=f"filter_refine:{user_id}" if user_id else "",
            user_vars={
                "current_learned_block": current_learned_block or "(empty — none yet)",
                "decline_signals": _fmt(decline_signals),
                "override_signals": _fmt(override_signals),
            },
        )

        try:
            raw = self.llm.generate_json(
                spec,
                schema=self._REFINEMENT_SCHEMA,
                temperature=0.3,
            )
        except Exception as e:
            logger.error(f"Refinement generation failed: {e}")
            raise JobFilterError(f"Refinement generation failed: {e}") from e

        block = (raw.get("proposed_learned_block") or "").strip()
        rationale = (raw.get("rationale") or "").strip()
        # Validate the block looks like the expected structure before storing.
        if not block or "## Auto-learned criteria" not in block:
            raise JobFilterError(
                "Refinement produced a malformed auto-learned block "
                "(missing '## Auto-learned criteria' heading)"
            )

        logger.info("Generated filter refinement (%d chars)", len(block))
        return {"proposed_learned_block": block, "rationale": rationale}

    def should_reject(self, result: FilterResult, reject_threshold: int = 30) -> bool:
        """Check if a job should be auto-rejected.

        Returns True if the result has a hard disqualifier OR the score
        is below the reject threshold.
        """
        return result.disqualified or result.score < reject_threshold

    def should_warn(self, result: FilterResult, warning_threshold: int = 70, reject_threshold: int = 30) -> bool:
        """Check if a job should show a warning badge in HITL review.

        Returns True if the score is below the warning threshold but
        the job was NOT rejected (i.e., it passes the reject check but
        has concerns).
        """
        return not self.should_reject(result, reject_threshold) and result.score < warning_threshold

    def _build_evaluation_spec(
        self,
        job_posting: dict[str, Any],
        user_filter_prefs: UserFilterPreferences | None,
        user_id: str,
    ) -> PromptSpec:
        """Build evaluation spec by injecting user criteria into the static
        block and job posting into the variable block.

        Uses ``custom_prompt`` (generated criteria) if set, otherwise falls
        back to ``natural_language_prefs`` as a simple criteria section.
        """
        user_criteria_section = ""
        if user_filter_prefs and user_filter_prefs.custom_prompt:
            user_criteria_section = (
                "User-Specific Criteria (apply IN ADDITION to the checks below):\n\n"
                f"{user_filter_prefs.custom_prompt}"
            )
        elif user_filter_prefs and user_filter_prefs.natural_language_prefs:
            user_criteria_section = (
                "User Preferences (use these to adjust scoring):\n"
                f"{user_filter_prefs.natural_language_prefs}"
            )

        return self.prompts.load_spec(
            "default_filter_prompt",
            cache_key=f"filter:{user_id}" if user_id else "",
            system_vars={"user_criteria_section": user_criteria_section},
            user_vars={
                "job_title": job_posting.get("title", "N/A"),
                "company": job_posting.get("company", "N/A"),
                "location": job_posting.get("location", "N/A"),
                "description": job_posting.get("description", "N/A"),
            },
        )
