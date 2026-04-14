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
from string import Template
from typing import Any

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
    ) -> FilterResult:
        """Evaluate a job posting for suitability.

        Uses the user's custom prompt if set, otherwise falls back to the
        default filter prompt template.

        Args:
            job_posting: Normalized job data with keys like title, company,
                         location, description.
            user_filter_prefs: Optional per-user filter preferences containing
                               custom prompt and natural language prefs.

        Returns:
            FilterResult with score, red flags, and disqualification info.

        Raises:
            JobFilterError: If LLM call or result parsing fails.
        """
        job_title = job_posting.get("title", "N/A")
        company = job_posting.get("company", "N/A")
        logger.info(f"Evaluating job: {job_title} at {company}")

        prompt = self._build_evaluation_prompt(job_posting, user_filter_prefs)

        try:
            raw_result = self.llm.generate_json(
                prompt,
                schema=FILTER_RESULT_SCHEMA,
                temperature=self.TEMPERATURE,
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

    def generate_prompt_from_preferences(self, natural_language_prefs: str) -> str:
        """Generate a structured filter prompt from natural language preferences.

        Calls the LLM with a meta-prompt that converts the user's free-text
        description of their preferences into a concrete evaluation prompt.

        Args:
            natural_language_prefs: User's natural language description of
                                    what they want / don't want.

        Returns:
            Generated prompt string suitable for textarea 2 in the UI.

        Raises:
            JobFilterError: If LLM call fails.
        """
        logger.info("Generating filter prompt from user preferences")

        template_str = self.prompts.load("generate_prompt_from_prefs")
        template = Template(template_str)
        prompt = template.safe_substitute(
            natural_language_prefs=natural_language_prefs,
        )

        try:
            generated = self.llm.generate(prompt, temperature=0.4)
        except Exception as e:
            logger.error(f"Prompt generation failed: {e}")
            raise JobFilterError(f"Prompt generation failed: {e}") from e

        generated = generated.strip()
        logger.info(f"Generated filter prompt ({len(generated)} chars)")
        return generated

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

    def _build_evaluation_prompt(
        self,
        job_posting: dict[str, Any],
        user_filter_prefs: UserFilterPreferences | None,
    ) -> str:
        """Build evaluation prompt by injecting user criteria into the default template.

        Uses custom_prompt (generated criteria) if set, otherwise falls back
        to natural_language_prefs as a simple criteria section.
        """
        # Build user criteria section
        user_criteria_section = ""
        if user_filter_prefs and user_filter_prefs.custom_prompt:
            user_criteria_section = (
                f"User-Specific Criteria (apply IN ADDITION to the checks below):\n\n"
                f"{user_filter_prefs.custom_prompt}"
            )
        elif user_filter_prefs and user_filter_prefs.natural_language_prefs:
            user_criteria_section = (
                f"User Preferences (use these to adjust scoring):\n"
                f"{user_filter_prefs.natural_language_prefs}"
            )

        template_str = self.prompts.load("default_filter_prompt")
        template = Template(template_str)
        return template.safe_substitute(
            job_title=job_posting.get("title", "N/A"),
            company=job_posting.get("company", "N/A"),
            location=job_posting.get("location", "N/A"),
            description=job_posting.get("description", "N/A"),
            user_criteria_section=user_criteria_section,
        )
