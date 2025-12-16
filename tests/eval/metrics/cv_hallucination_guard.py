"""Custom DeepEval metric for CV-specific hallucination detection"""

from typing import Optional, Dict, List
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


class CVHallucinationGuard(BaseMetric):
    """
    Custom metric to detect hallucinations in CV data

    Checks that all entities (companies, institutions, skills) in the tailored CV
    exist in the master CV. This prevents the LLM from fabricating experience or education.

    Args:
        threshold: Success threshold (1.0 = no hallucinations allowed)
        check_type: Type of entities to check ("companies" | "institutions" | "skills")
        master_data: Master CV dictionary containing valid entities
        model: Not used for this deterministic metric
        include_reason: Whether to include detailed reason in output
    """

    def __init__(
        self,
        threshold: float = 1.0,
        check_type: str = "companies",
        master_data: Optional[Dict] = None,
        model: Optional[str] = None,
        include_reason: bool = True
    ):
        self.threshold = threshold
        self.check_type = check_type
        self.master_data = master_data or {}
        self.model = model
        self.include_reason = include_reason
        self.score = 0.0
        self.reason = ""
        self.success = False

        if check_type not in ["companies", "institutions", "skills"]:
            raise ValueError(f"Invalid check_type: {check_type}. Must be one of: companies, institutions, skills")

    def measure(self, test_case: LLMTestCase) -> float:
        """
        Measure if output contains hallucinations

        Returns:
            1.0 if no hallucinations detected
            0.0 if hallucinations found
        """
        import json

        # Parse tailored CV (from actual_output)
        try:
            if isinstance(test_case.actual_output, str):
                tailored_cv = json.loads(test_case.actual_output)
            elif isinstance(test_case.actual_output, dict):
                tailored_cv = test_case.actual_output
            else:
                raise ValueError(f"Unsupported actual_output type: {type(test_case.actual_output)}")
        except json.JSONDecodeError as e:
            self.score = 0.0
            self.reason = f"Failed to parse tailored CV JSON: {e}"
            self.success = False
            return self.score

        # Extract entities based on check_type
        try:
            if self.check_type == "companies":
                valid_entities = self._extract_companies(self.master_data)
                tailored_entities = self._extract_companies(tailored_cv)
                entity_name = "companies"

            elif self.check_type == "institutions":
                valid_entities = self._extract_institutions(self.master_data)
                tailored_entities = self._extract_institutions(tailored_cv)
                entity_name = "institutions"

            elif self.check_type == "skills":
                valid_entities = self._extract_skills(self.master_data)
                tailored_entities = self._extract_skills(tailored_cv)
                entity_name = "skills"

            else:
                raise ValueError(f"Unknown check_type: {self.check_type}")

        except Exception as e:
            self.score = 0.0
            self.reason = f"Error extracting entities: {e}"
            self.success = False
            return self.score

        # Check for hallucinations (entities in tailored but not in master)
        hallucinations = tailored_entities - valid_entities

        if hallucinations:
            self.score = 0.0
            self.reason = (
                f"Hallucinated {entity_name}: {sorted(hallucinations)}. "
                f"Valid {entity_name}: {sorted(valid_entities)}"
            )
            self.success = False
        else:
            self.score = 1.0
            self.reason = f"All {entity_name} validated against master CV ({len(tailored_entities)} checked)"
            self.success = True

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
        return f"CV Hallucination Guard ({self.check_type})"

    # Helper methods to extract entities

    def _extract_companies(self, cv: Dict) -> set:
        """Extract company names from CV (case-insensitive)"""
        companies = set()
        for exp in cv.get('experiences', []):
            if 'company' in exp and exp['company']:
                companies.add(exp['company'].strip().lower())
        return companies

    def _extract_institutions(self, cv: Dict) -> set:
        """Extract education institutions from CV (case-insensitive)"""
        institutions = set()
        for edu in cv.get('education', []):
            if 'institution' in edu and edu['institution']:
                institutions.add(edu['institution'].strip().lower())
        return institutions

    def _extract_skills(self, cv: Dict) -> set:
        """Extract skill names from CV (case-insensitive)"""
        skills = set()

        # Handle both flat list and categorized skills
        skills_data = cv.get('skills', [])

        if isinstance(skills_data, list):
            for item in skills_data:
                if isinstance(item, str):
                    # Flat list of skill strings
                    skills.add(item.strip().lower())
                elif isinstance(item, dict):
                    # Categorized skills: {name: str, ...} or {category: str, skills: [...]}
                    if 'name' in item:
                        skills.add(item['name'].strip().lower())
                    elif 'skills' in item and isinstance(item['skills'], list):
                        for skill in item['skills']:
                            if isinstance(skill, str):
                                skills.add(skill.strip().lower())
                            elif isinstance(skill, dict) and 'name' in skill:
                                skills.add(skill['name'].strip().lower())

        return skills
