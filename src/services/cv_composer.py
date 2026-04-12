"""Service for composing tailored CV using LLM"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.config.settings import Settings
from src.llm.provider import BaseLLMClient
from src.models.cv import (
    CVLLMOutput,
    JobSummary,
)
from src.services.cv_prompts import CVPromptManager

if TYPE_CHECKING:
    from src.services.cv_validator import CVValidator

logger = logging.getLogger(__name__)


class CVCompositionError(Exception):
    """Raised when CV composition fails"""

    pass


# Schemas derived from Pydantic models for LLM structured output
FULL_CV_SCHEMA = CVLLMOutput.model_json_schema()
JOB_SUMMARY_SCHEMA = JobSummary.model_json_schema()


@dataclass(frozen=True)
class CVComposerSettings:
    """Settings used by CVComposer that do not require secrets."""

    cv_max_experiences: int = Settings.model_fields["cv_max_experiences"].default
    cv_max_achievements_per_experience: int = Settings.model_fields[
        "cv_max_achievements_per_experience"
    ].default
    cv_max_skills: int = Settings.model_fields["cv_max_skills"].default
    cv_max_projects: int = Settings.model_fields["cv_max_projects"].default
    cv_max_certifications: int = Settings.model_fields["cv_max_certifications"].default


class CVComposer:
    """Composes tailored CV based on job description using LLM"""

    # Temperature settings for LLM generation
    # Lower = more deterministic, higher = more creative
    TEMPERATURE_ANALYSIS = 0.3  # For job analysis - prefer consistency
    TEMPERATURE_GENERATION = 0.4  # For CV generation - allow some creativity

    def __init__(
        self,
        llm_client: BaseLLMClient,
        prompts_dir: str | None = None,
        settings: Settings | CVComposerSettings | None = None,
    ):
        """
        Initialize CV Composer

        Args:
            llm_client: LLM client for generation
            prompts_dir: Optional custom prompts directory
            settings: Optional settings instance (defaults to CVComposerSettings)
        """
        self.llm = llm_client
        self.prompts = CVPromptManager(prompts_dir)
        self.settings = settings or CVComposerSettings()

    def compose_cv(
        self,
        master_cv: dict[str, Any],
        job_posting: dict[str, Any],
        user_feedback: str | None = None,
        validator: CVValidator | None = None,
    ) -> CVLLMOutput:
        """
        Generate a tailored CV for a specific job posting

        Args:
            master_cv: Complete CV data in JSON format
            job_posting: Job posting information with keys: title, company, description, requirements
            user_feedback: Optional feedback from user for retry/refinement
            validator: Optional CVValidator instance for output validation.
                       If None, uses legacy inline validation (warn-only).

        Returns:
            Tailored CV as validated Pydantic model

        Raises:
            CVCompositionError: If LLM generation or validation fails
        """
        logger.info(
            f"Composing CV for job: {job_posting.get('title')} at {job_posting.get('company')}"
        )

        # Step 1: Analyze job description to extract requirements
        job_summary = self._summarize_job(job_posting)
        logger.debug("Job analysis completed")

        # Step 2: Generate all CV sections in a single LLM call (optimized)
        generated_sections = self._compose_all_sections(master_cv, job_summary, user_feedback)

        # Step 2.5: Apply length limits to ensure 2-page target
        generated_sections = self._apply_length_limits(generated_sections)

        if validator:
            # Use the injected CVValidator for all validation
            contact = validator.validate_contact(master_cv.get("contact", {}))
            languages = validator.validate_languages(master_cv.get("languages", []))
            interests = validator.validate_interests(master_cv.get("interests"))

            tailored_cv = {
                "contact": contact,
                "summary": generated_sections.get("summary", ""),
                "experiences": generated_sections.get("experiences", []),
                "education": generated_sections.get("education", []),
                "skills": generated_sections.get("skills", []),
                "projects": generated_sections.get("projects", []),
                "certifications": generated_sections.get("certifications", []),
                "languages": languages,
                "interests": interests,
            }

            validated_cv = validator.validate_output(tailored_cv)
        else:
            # Legacy path: inline validation with warn-only hallucination checks
            contact = self._validate_contact(master_cv.get("contact", {}))
            languages = self._validate_languages(master_cv.get("languages", []))
            interests = self._validate_interests(master_cv.get("interests"))

            tailored_cv = {
                "contact": contact,
                "summary": generated_sections.get("summary", ""),
                "experiences": generated_sections.get("experiences", []),
                "education": generated_sections.get("education", []),
                "skills": generated_sections.get("skills", []),
                "projects": generated_sections.get("projects", []),
                "certifications": generated_sections.get("certifications", []),
                "languages": languages,
                "interests": interests,
            }

            validated_cv = self._validate_output(tailored_cv, master_cv)

        logger.info("CV composition completed successfully")
        return validated_cv

    def _summarize_job(self, job_posting: dict[str, Any]) -> dict[str, Any]:
        """
        Analyze job description and extract key requirements

        Args:
            job_posting: Job posting with description and requirements

        Returns:
            Dictionary with structured job requirements

        Raises:
            CVCompositionError: If job analysis fails
        """
        logger.debug("Summarizing job description")

        # Build full job description from all available fields
        job_description = f"""
Title: {job_posting.get("title", "N/A")}
Company: {job_posting.get("company", "N/A")}

Description:
{job_posting.get("description", "")}

Requirements:
{job_posting.get("requirements", "")}
        """.strip()

        # Get prompt from external file
        prompt = self.prompts.get_job_summary_prompt(job_description=job_description)

        # Generate structured summary using LLM
        try:
            logger.info("[TIMING] Starting LLM API call for job summary")
            llm_start = time.time()
            summary = self.llm.generate_json(
                prompt, schema=JOB_SUMMARY_SCHEMA, temperature=self.TEMPERATURE_ANALYSIS
            )
            llm_elapsed = time.time() - llm_start
            logger.info(f"[TIMING] LLM API call for job summary completed in {llm_elapsed:.2f}s")
        except Exception as e:
            logger.error(f"Failed to analyze job description: {e}")
            raise CVCompositionError(f"Job analysis failed: {e}") from e

        # Validate with Pydantic model
        try:
            job_summary = JobSummary(**summary)
        except Exception as e:
            logger.error(f"Job summary validation failed: {e}")
            raise CVCompositionError(f"Invalid job summary structure: {e}") from e

        return job_summary.model_dump()

    def _compose_all_sections(
        self,
        master_cv: dict[str, Any],
        job_summary: dict[str, Any],
        user_feedback: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate complete tailored CV in a single LLM call.

        Args:
            master_cv: Complete master CV data
            job_summary: Structured job requirements from _summarize_job()
            user_feedback: Optional user feedback for retry/refinement

        Returns:
            Dictionary containing all tailored CV sections

        Raises:
            CVCompositionError: If CV generation fails
        """
        logger.debug("Composing all CV sections in single LLM call")

        # Get unified prompt (includes user feedback if provided)
        prompt = self.prompts.get_full_cv_prompt(
            master_cv=master_cv,
            job_summary=job_summary,
            user_feedback=user_feedback,
        )

        # Generate complete tailored CV using unified schema
        try:
            logger.info("[TIMING] Starting LLM API call for full CV generation")
            llm_start = time.time()
            result = self.llm.generate_json(
                prompt,
                schema=FULL_CV_SCHEMA,
                temperature=self.TEMPERATURE_GENERATION,
            )
            llm_elapsed = time.time() - llm_start
            logger.info(f"[TIMING] LLM API call for full CV generation completed in {llm_elapsed:.2f}s")
        except Exception as e:
            logger.error(f"Failed to generate CV sections: {e}")
            raise CVCompositionError(f"CV generation failed: {e}") from e

        logger.debug("Successfully generated all CV sections")
        return result

    def _apply_length_limits(self, generated_sections: dict[str, Any]) -> dict[str, Any]:
        """
        Apply configured length limits to CV sections.

        Args:
            generated_sections: CV sections from LLM generation

        Returns:
            Sections with length limits enforced
        """
        logger.debug("Applying length limits to CV sections")

        truncated = False

        # Limit experiences
        if len(generated_sections.get("experiences", [])) > self.settings.cv_max_experiences:
            original_count = len(generated_sections["experiences"])
            generated_sections["experiences"] = generated_sections["experiences"][
                : self.settings.cv_max_experiences
            ]
            logger.info(
                f"Truncated experiences: {original_count} → {self.settings.cv_max_experiences}"
            )
            truncated = True

        # Limit achievements per experience
        for i, exp in enumerate(generated_sections.get("experiences", [])):
            if len(exp.get("achievements", [])) > self.settings.cv_max_achievements_per_experience:
                original_count = len(exp["achievements"])
                exp["achievements"] = exp["achievements"][
                    : self.settings.cv_max_achievements_per_experience
                ]
                logger.debug(
                    f"Truncated achievements for experience {i}: {original_count} → {self.settings.cv_max_achievements_per_experience}"
                )
                truncated = True

        # Limit skills
        if len(generated_sections.get("skills", [])) > self.settings.cv_max_skills:
            original_count = len(generated_sections["skills"])
            generated_sections["skills"] = generated_sections["skills"][
                : self.settings.cv_max_skills
            ]
            logger.info(f"Truncated skills: {original_count} → {self.settings.cv_max_skills}")
            truncated = True

        # Limit projects
        if len(generated_sections.get("projects", [])) > self.settings.cv_max_projects:
            original_count = len(generated_sections["projects"])
            generated_sections["projects"] = generated_sections["projects"][
                : self.settings.cv_max_projects
            ]
            logger.info(f"Truncated projects: {original_count} → {self.settings.cv_max_projects}")
            truncated = True

        # Limit certifications
        if len(generated_sections.get("certifications", [])) > self.settings.cv_max_certifications:
            original_count = len(generated_sections["certifications"])
            generated_sections["certifications"] = generated_sections["certifications"][
                : self.settings.cv_max_certifications
            ]
            logger.info(
                f"Truncated certifications: {original_count} → {self.settings.cv_max_certifications}"
            )
            truncated = True

        if not truncated:
            logger.debug("No truncation needed - CV within length limits")

        return generated_sections

    # Legacy validation methods kept for backward compatibility when no CVValidator is provided

    def _validate_contact(self, contact_data: dict[str, Any]) -> dict[str, Any]:
        from src.models.cv import ContactInfo

        try:
            contact = ContactInfo(**contact_data)
            return contact.model_dump()
        except Exception as e:
            logger.error(f"Invalid contact information in master CV: {e}")
            raise CVCompositionError(f"Invalid contact data: {e}") from e

    def _validate_languages(self, languages_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from src.models.cv import Language

        try:
            languages = [Language(**lang) for lang in languages_data]
            return [lang.model_dump() for lang in languages]
        except Exception as e:
            logger.error(f"Invalid languages in master CV: {e}")
            raise CVCompositionError(f"Invalid languages data: {e}") from e

    def _validate_interests(self, interests_data: dict[str, Any] | None) -> dict[str, Any] | None:
        from src.models.cv import Interests

        if interests_data is None:
            return None

        try:
            interests = Interests(**interests_data)
            return interests.model_dump()
        except Exception as e:
            logger.error(f"Invalid interests in master CV: {e}")
            raise CVCompositionError(f"Invalid interests data: {e}") from e

    def _validate_output(self, tailored_cv: dict[str, Any], master_cv: dict[str, Any]) -> CVLLMOutput:
        """Legacy validation with warn-only hallucination checks."""
        logger.debug("Validating tailored CV output")

        try:
            validated = CVLLMOutput(**tailored_cv)
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            raise CVCompositionError(f"Tailored CV does not match expected schema: {e}") from e

        # Hallucination check: Companies (warn only)
        master_companies = {
            exp.get("company", "").lower() for exp in master_cv.get("experiences", [])
        }
        tailored_companies = {
            exp.get("company", "").lower() for exp in tailored_cv.get("experiences", [])
        }

        if not tailored_companies.issubset(master_companies):
            invalid_companies = tailored_companies - master_companies
            logger.warning(f"Tailored CV contains new companies: {invalid_companies}")

        # Hallucination check: Institutions (warn only)
        master_institutions = {
            edu.get("institution", "").lower() for edu in master_cv.get("education", [])
        }
        tailored_institutions = {
            edu.get("institution", "").lower() for edu in tailored_cv.get("education", [])
        }

        if not tailored_institutions.issubset(master_institutions):
            invalid_institutions = tailored_institutions - master_institutions
            logger.warning(f"Tailored CV contains new institutions: {invalid_institutions}")

        logger.info("Validation completed successfully")
        return validated
