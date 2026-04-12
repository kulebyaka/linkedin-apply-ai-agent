"""CV validation service extracted from CVComposer.

Validates tailored CV output against master CV to prevent hallucinations.
Supports configurable enforcement policies: STRICT, WARN, DISABLED.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from src.models.cv import (
    ContactInfo,
    CVLLMOutput,
    Interests,
    Language,
)
from src.services.cv_composer import CVCompositionError

logger = logging.getLogger(__name__)


class HallucinationPolicy(str, Enum):
    """Policy for handling detected hallucinations in tailored CVs."""

    STRICT = "strict"  # Raise CVHallucinationError on fabricated entities
    WARN = "warn"  # Log warning but allow (previous default behavior)
    DISABLED = "disabled"  # Skip hallucination checks entirely


class CVHallucinationError(CVCompositionError):
    """Raised when tailored CV contains fabricated entities not in master CV.

    Attributes:
        fabricated_companies: Set of company names not found in master CV.
        fabricated_institutions: Set of institution names not found in master CV.
    """

    def __init__(
        self,
        message: str,
        *,
        fabricated_companies: set[str] | None = None,
        fabricated_institutions: set[str] | None = None,
    ):
        super().__init__(message)
        self.fabricated_companies = fabricated_companies or set()
        self.fabricated_institutions = fabricated_institutions or set()


class CVValidator:
    """Validates tailored CV output against master CV data.

    Extracted from CVComposer to separate composition from validation concerns.
    Handles:
    - Contact info validation (pass-through from master CV)
    - Languages validation (pass-through from master CV)
    - Interests validation (pass-through from master CV)
    - Schema validation via Pydantic
    - Hallucination detection (fabricated companies/institutions)
    """

    def __init__(
        self,
        master_cv: dict[str, Any],
        policy: HallucinationPolicy = HallucinationPolicy.STRICT,
    ):
        self.master_cv = master_cv
        self.policy = policy

    def validate_contact(self, contact_data: dict[str, Any]) -> dict[str, Any]:
        """Validate contact information from master CV.

        Args:
            contact_data: Raw contact data from master CV.

        Returns:
            Validated contact data as dict.

        Raises:
            CVCompositionError: If contact data is invalid.
        """
        try:
            contact = ContactInfo(**contact_data)
            return contact.model_dump()
        except Exception as e:
            logger.error(f"Invalid contact information in master CV: {e}")
            raise CVCompositionError(f"Invalid contact data: {e}") from e

    def validate_languages(self, languages_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate languages from master CV.

        Args:
            languages_data: Raw languages list from master CV.

        Returns:
            Validated languages as list of dicts.

        Raises:
            CVCompositionError: If languages data is invalid.
        """
        try:
            languages = [Language(**lang) for lang in languages_data]
            return [lang.model_dump() for lang in languages]
        except Exception as e:
            logger.error(f"Invalid languages in master CV: {e}")
            raise CVCompositionError(f"Invalid languages data: {e}") from e

    def validate_interests(self, interests_data: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate interests from master CV.

        Args:
            interests_data: Raw interests data from master CV (optional).

        Returns:
            Validated interests as dict, or None if not provided.

        Raises:
            CVCompositionError: If interests data is invalid.
        """
        if interests_data is None:
            return None

        try:
            interests = Interests(**interests_data)
            return interests.model_dump()
        except Exception as e:
            logger.error(f"Invalid interests in master CV: {e}")
            raise CVCompositionError(f"Invalid interests data: {e}") from e

    def validate_output(self, tailored_cv: dict[str, Any]) -> CVLLMOutput:
        """Validate tailored CV against master CV to prevent hallucinations.

        Checks:
        1. Schema matches CVLLMOutput model
        2. All companies/institutions exist in master CV (policy-dependent)

        Args:
            tailored_cv: Generated tailored CV dict.

        Returns:
            Validated CV as Pydantic model.

        Raises:
            CVCompositionError: If schema validation fails.
            CVHallucinationError: If STRICT policy and fabricated entities detected.
        """
        logger.debug("Validating tailored CV output")

        # Schema validation via Pydantic
        try:
            validated = CVLLMOutput(**tailored_cv)
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            raise CVCompositionError(f"Tailored CV does not match expected schema: {e}") from e

        # Hallucination checks (skip if disabled)
        if self.policy == HallucinationPolicy.DISABLED:
            logger.debug("Hallucination checks disabled by policy")
            return validated

        fabricated_companies = self._check_companies(tailored_cv)
        fabricated_institutions = self._check_institutions(tailored_cv)

        has_hallucinations = bool(fabricated_companies or fabricated_institutions)

        if has_hallucinations:
            details_parts = []
            if fabricated_companies:
                details_parts.append(f"fabricated companies: {sorted(fabricated_companies)}")
            if fabricated_institutions:
                details_parts.append(f"fabricated institutions: {sorted(fabricated_institutions)}")
            details = "; ".join(details_parts)

            if self.policy == HallucinationPolicy.STRICT:
                raise CVHallucinationError(
                    f"CV contains hallucinated entities: {details}",
                    fabricated_companies=fabricated_companies,
                    fabricated_institutions=fabricated_institutions,
                )
            elif self.policy == HallucinationPolicy.WARN:
                logger.warning(f"Tailored CV contains hallucinated entities: {details}")

        logger.info("Validation completed successfully")
        return validated

    def _check_companies(self, tailored_cv: dict[str, Any]) -> set[str]:
        """Check for fabricated companies not in master CV."""
        master_companies = {
            exp.get("company", "").lower() for exp in self.master_cv.get("experiences", [])
        }
        tailored_companies = {
            exp.get("company", "").lower() for exp in tailored_cv.get("experiences", [])
        }

        return tailored_companies - master_companies

    def _check_institutions(self, tailored_cv: dict[str, Any]) -> set[str]:
        """Check for fabricated institutions not in master CV."""
        master_institutions = {
            edu.get("institution", "").lower() for edu in self.master_cv.get("education", [])
        }
        tailored_institutions = {
            edu.get("institution", "").lower() for edu in tailored_cv.get("education", [])
        }

        return tailored_institutions - master_institutions
