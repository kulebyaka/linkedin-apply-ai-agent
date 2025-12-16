"""Schema compliance guardrail for CV validation"""

from typing import Optional
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from pydantic import ValidationError
import json


class CVSchemaComplianceGuard(BaseMetric):
    """
    Ensures CV output strictly matches Pydantic schema

    This is a hard requirement - any schema violation fails the test.
    Validates the tailored CV against the CV Pydantic model from src/models/cv.py

    Args:
        threshold: Always 1.0 (strict compliance required)
        model: Not used for this deterministic metric
        include_reason: Whether to include detailed validation errors
    """

    def __init__(
        self,
        threshold: float = 1.0,
        model: Optional[str] = None,
        include_reason: bool = True
    ):
        self.threshold = 1.0  # Always strict
        self.model = model
        self.include_reason = include_reason
        self.score = 0.0
        self.reason = ""
        self.success = False

    def measure(self, test_case: LLMTestCase) -> float:
        """
        Validate CV output against Pydantic schema

        Returns:
            1.0 if schema validation passes
            0.0 if validation fails
        """
        from src.models.cv import CV

        try:
            # Parse CV data
            if isinstance(test_case.actual_output, str):
                cv_data = json.loads(test_case.actual_output)
            elif isinstance(test_case.actual_output, dict):
                cv_data = test_case.actual_output
            else:
                raise ValueError(f"Unsupported actual_output type: {type(test_case.actual_output)}")

            # Validate against Pydantic model
            validated_cv = CV(**cv_data)

            # Success - CV matches schema
            self.score = 1.0
            self.reason = "CV matches Pydantic schema. All required fields present and valid."
            self.success = True

        except json.JSONDecodeError as e:
            self.score = 0.0
            self.reason = f"JSON parsing failed: {str(e)}"
            self.success = False

        except ValidationError as e:
            self.score = 0.0
            # Format validation errors nicely
            errors = []
            for error in e.errors():
                field = " -> ".join(str(x) for x in error['loc'])
                msg = error['msg']
                errors.append(f"{field}: {msg}")

            self.reason = f"Schema validation failed ({len(errors)} errors):\n" + "\n".join(f"  - {err}" for err in errors[:5])
            if len(errors) > 5:
                self.reason += f"\n  ... and {len(errors) - 5} more errors"

            self.success = False

        except Exception as e:
            self.score = 0.0
            self.reason = f"Unexpected validation error: {type(e).__name__}: {str(e)}"
            self.success = False

        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        """Async version of measure (same as sync for this deterministic metric)"""
        return self.measure(test_case)

    def is_successful(self) -> bool:
        """Check if the metric passed"""
        return self.success

    @property
    def __name__(self):
        """Metric name for reporting"""
        return "CV Schema Compliance Guard"


class CVSectionSchemaGuard(BaseMetric):
    """
    Validates a specific CV section against its schema

    Useful for testing individual section composers (experiences, education, etc.)

    Args:
        section_name: Name of section to validate ("experiences", "education", "skills", etc.)
        threshold: Always 1.0 (strict compliance)
        model: Not used
        include_reason: Whether to include detailed errors
    """

    def __init__(
        self,
        section_name: str,
        threshold: float = 1.0,
        model: Optional[str] = None,
        include_reason: bool = True
    ):
        self.section_name = section_name
        self.threshold = 1.0
        self.model = model
        self.include_reason = include_reason
        self.score = 0.0
        self.reason = ""
        self.success = False

        # Map section names to Pydantic models
        self.section_models = {
            "experiences": "Experience",
            "education": "Education",
            "skills": "Skill",
            "projects": "Project",
            "contact": "ContactInfo",
        }

        if section_name not in self.section_models:
            raise ValueError(
                f"Unknown section: {section_name}. "
                f"Supported: {list(self.section_models.keys())}"
            )

    def measure(self, test_case: LLMTestCase) -> float:
        """Validate section data against its schema"""
        from src.models.cv import Experience, Education, Skill, Project, ContactInfo

        model_map = {
            "experiences": Experience,
            "education": Education,
            "skills": Skill,
            "projects": Project,
            "contact": ContactInfo,
        }

        model_class = model_map[self.section_name]

        try:
            # Parse section data
            if isinstance(test_case.actual_output, str):
                section_data = json.loads(test_case.actual_output)
            else:
                section_data = test_case.actual_output

            # Validate based on whether section is list or dict
            if self.section_name == "contact":
                # Contact is a single object
                model_class(**section_data)
                count = 1
            else:
                # Most sections are lists
                if not isinstance(section_data, list):
                    raise ValueError(f"{self.section_name} must be a list, got {type(section_data)}")

                for item in section_data:
                    model_class(**item)
                count = len(section_data)

            self.score = 1.0
            self.reason = f"{self.section_name.capitalize()} section valid ({count} items checked)"
            self.success = True

        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            self.score = 0.0
            self.reason = f"{self.section_name.capitalize()} validation failed: {str(e)}"
            self.success = False

        except Exception as e:
            self.score = 0.0
            self.reason = f"Unexpected error validating {self.section_name}: {type(e).__name__}: {str(e)}"
            self.success = False

        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        """Async version"""
        return self.measure(test_case)

    def is_successful(self) -> bool:
        """Check if metric passed"""
        return self.success

    @property
    def __name__(self):
        """Metric name"""
        return f"CV {self.section_name.capitalize()} Schema Guard"
