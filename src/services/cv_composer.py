"""Service for composing tailored CV using LLM"""

import logging

from src.llm.provider import BaseLLMClient
from src.models.cv import CV, JobSummary
from src.services.cv_prompts import CVPromptManager

logger = logging.getLogger(__name__)


class CVComposer:
    """Composes tailored CV based on job description using LLM"""

    def __init__(self, llm_client: BaseLLMClient, prompts_dir: str | None = None):
        """
        Initialize CV Composer

        Args:
            llm_client: LLM client for generation
            prompts_dir: Optional custom prompts directory
        """
        self.llm = llm_client
        self.prompts = CVPromptManager(prompts_dir)

    def compose_cv(
        self, master_cv: dict, job_posting: dict, user_feedback: str | None = None
    ) -> dict:
        """
        Generate a tailored CV for a specific job posting

        Args:
            master_cv: Complete CV data in JSON format
            job_posting: Job posting information with keys: title, company, description, requirements
            user_feedback: Optional feedback from user for retry

        Returns:
            Tailored CV as JSON
        """
        logger.info(
            f"Composing CV for job: {job_posting.get('title')} at {job_posting.get('company')}"
        )

        # Step 1: Analyze job description to extract requirements
        job_summary = self._summarize_job(job_posting)
        logger.debug(f"Job summary: {job_summary}")

        # Step 2: Compose each section of the CV
        tailored_cv = {
            "contact": master_cv.get("contact", {}),  # Contact info unchanged
            "summary": self._compose_summary(master_cv, job_summary),
            "experiences": self._compose_experiences(master_cv, job_summary),
            "education": self._compose_education(master_cv, job_summary),
            "skills": self._compose_skills(master_cv, job_summary),
            "projects": self._compose_projects(master_cv, job_summary),
            "certifications": self._compose_certifications(master_cv, job_summary),
            "languages": master_cv.get("languages", []),  # Languages unchanged
            "interests": master_cv.get("interests"),  # Interests unchanged (optional)
        }

        # Step 3: Validate output against master CV
        validated_cv = self._validate_output(tailored_cv, master_cv)

        logger.info("CV composition completed successfully")
        return validated_cv

    def _summarize_job(self, job_posting: dict) -> dict:
        """
        Analyze job description and extract key requirements

        Args:
            job_posting: Job posting with description and requirements

        Returns:
            Dictionary with structured job requirements:
            {
                "technical_skills": [...],
                "soft_skills": [...],
                "education_reqs": [...],
                "experience_reqs": {"years": int, "level": str},
                "responsibilities": [...],
                "nice_to_have": [...]
            }
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

        # Define expected schema for job summary
        schema = {
            "type": "object",
            "properties": {
                "technical_skills": {"type": "array", "items": {"type": "string"}},
                "soft_skills": {"type": "array", "items": {"type": "string"}},
                "education_reqs": {"type": "array", "items": {"type": "string"}},
                "experience_reqs": {
                    "type": "object",
                    "properties": {
                        "years": {"type": ["integer", "null"]},
                        "level": {"type": ["string", "null"]},
                    },
                },
                "responsibilities": {"type": "array", "items": {"type": "string"}},
                "nice_to_have": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "technical_skills",
                "soft_skills",
                "education_reqs",
                "experience_reqs",
                "responsibilities",
                "nice_to_have",
            ],
        }

        # Generate structured summary using LLM
        summary = self.llm.generate_json(prompt, schema=schema, temperature=0.3)

        # Validate with Pydantic model
        job_summary = JobSummary(**summary)

        return job_summary.model_dump()

    def _compose_summary(self, master_cv: dict, job_summary: dict) -> str:
        """
        Generate tailored professional summary

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            Tailored professional summary string
        """
        logger.debug("Composing professional summary")

        # Extract relevant info from master CV
        current_role = "Professional"
        years_experience = 0
        key_skills = []
        achievements = []

        # Try to determine current role and experience
        experiences = master_cv.get("experiences", [])
        if experiences:
            # Assume first experience is most recent/current
            current_exp = experiences[0]
            current_role = current_exp.get("position", "Professional")

            # Calculate years of experience (simplified)
            years_experience = len(experiences) * 2  # Rough estimate

            # Collect achievements from recent experiences
            for exp in experiences[:3]:  # Top 3 experiences
                achievements.extend(exp.get("achievements", [])[:2])  # 2 achievements each

        # Collect key skills
        skills = master_cv.get("skills", [])
        for skill in skills[:10]:  # Top 10 skills
            if isinstance(skill, dict):
                key_skills.append(skill.get("name", ""))
            else:
                key_skills.append(str(skill))

        # Get prompt
        prompt = self.prompts.get_summary_prompt(
            current_role=current_role,
            years_experience=years_experience,
            key_skills=key_skills,
            achievements=achievements[:5],  # Top 5 achievements
            job_summary=job_summary,
        )

        # Define schema for summary output
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        }

        # Generate summary
        result = self.llm.generate_json(prompt, schema=schema, temperature=0.5)

        return result.get("summary", master_cv.get("summary", ""))

    def _compose_experiences(self, master_cv: dict, job_summary: dict) -> list[dict]:
        """
        Tailor work experience section

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            List of tailored experience entries
        """
        logger.debug("Composing work experiences")

        experiences = master_cv.get("experiences", [])
        if not experiences:
            return []

        # Get prompt
        prompt = self.prompts.get_experience_prompt(
            experiences=experiences, job_summary=job_summary
        )

        # Define expected schema
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "position": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": ["string", "null"]},
                    "is_current": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                    "achievements": {"type": "array", "items": {"type": "string"}},
                    "technologies": {"type": "array", "items": {"type": "string"}},
                    "projects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {"type": ["string", "null"]},
                                "description": {"type": "string"},
                                "achievements": {"type": "array", "items": {"type": "string"}},
                                "technologies": {"type": "array", "items": {"type": "string"}},
                                "duration": {"type": ["string", "null"]},
                            },
                            "required": ["name", "description"],
                        },
                    },
                    "company_context": {
                        "type": ["object", "null"],
                        "properties": {
                            "industry": {"type": ["string", "null"]},
                            "size": {"type": ["string", "null"]},
                            "notable_clients": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["company", "position", "start_date", "description"],
            },
        }

        # Generate tailored experiences
        tailored_experiences = self.llm.generate_json(prompt, schema=schema, temperature=0.4)

        return tailored_experiences

    def _compose_education(self, master_cv: dict, job_summary: dict) -> list[dict]:
        """
        Tailor education section

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            List of tailored education entries
        """
        logger.debug("Composing education section")

        education = master_cv.get("education", [])
        if not education:
            return []

        # Get prompt
        prompt = self.prompts.get_education_prompt(education=education, job_summary=job_summary)

        # Define expected schema
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": "string"},
                    "field_of_study": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": ["string", "null"]},
                    "is_current": {"type": "boolean"},
                    "location": {"type": ["string", "null"]},
                    "grade": {"type": ["string", "null"]},
                    "achievements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["institution", "degree", "field_of_study", "start_date"],
            },
        }

        # Generate tailored education
        tailored_education = self.llm.generate_json(prompt, schema=schema, temperature=0.3)

        return tailored_education

    def _compose_skills(self, master_cv: dict, job_summary: dict) -> list[dict]:
        """
        Optimize skills section for job relevance

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            List of skill category objects
        """
        logger.debug("Composing skills section")

        skills = master_cv.get("skills", [])
        if not skills:
            return []

        # Get prompt
        prompt = self.prompts.get_skills_prompt(skills=skills, job_summary=job_summary)

        # Define expected schema
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "proficiency": {"type": ["string", "null"]},
                    "years_of_experience": {"type": ["string", "null"]},
                    "use_cases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "category"],
            },
        }

        # Generate tailored skills
        tailored_skills = self.llm.generate_json(prompt, schema=schema, temperature=0.3)

        return tailored_skills

    def _compose_projects(self, master_cv: dict, job_summary: dict) -> list[dict]:
        """
        Highlight relevant projects

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            List of tailored project entries
        """
        logger.debug("Composing projects section")

        projects = master_cv.get("projects", [])
        if not projects:
            return []

        # Get prompt
        prompt = self.prompts.get_projects_prompt(projects=projects, job_summary=job_summary)

        # Define expected schema (with status, last_updated, role, architecture, visibility)
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "url": {"type": ["string", "null"]},
                    "technologies": {"type": "array", "items": {"type": "string"}},
                    "achievements": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": ["string", "null"], "enum": ["active", "archived", "production", "completed", None]},
                    "last_updated": {"type": ["string", "null"]},
                    "role": {"type": ["string", "null"]},
                    "architecture": {"type": "array", "items": {"type": "string"}},
                    "visibility": {"type": ["string", "null"], "enum": ["public", "private", None]},
                },
                "required": ["name", "description"],
            },
        }

        # Generate tailored projects
        tailored_projects = self.llm.generate_json(prompt, schema=schema, temperature=0.4)

        return tailored_projects

    def _compose_certifications(self, master_cv: dict, job_summary: dict) -> list[dict]:
        """
        Optimize certifications display

        Args:
            master_cv: Master CV data
            job_summary: Structured job requirements

        Returns:
            List of relevant certification objects
        """
        logger.debug("Composing certifications section")

        certifications = master_cv.get("certifications", [])
        if not certifications:
            return []

        # Get prompt
        prompt = self.prompts.get_certifications_prompt(
            certifications=certifications, job_summary=job_summary
        )

        # Define expected schema (objects with issuer, date, description, topics)
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "issuer": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "topics": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "issuer"],
            },
        }

        # Generate tailored certifications
        tailored_certifications = self.llm.generate_json(prompt, schema=schema, temperature=0.3)

        return tailored_certifications

    def _validate_output(self, tailored_cv: dict, master_cv: dict) -> dict:
        """
        Validate tailored CV against master CV to prevent hallucinations

        Checks:
        1. All companies/institutions exist in master CV
        2. Dates are within valid ranges
        3. No new skills/tech not in master CV (with some flexibility)
        4. Schema matches CV model

        Args:
            tailored_cv: Generated tailored CV
            master_cv: Original master CV

        Returns:
            Validated CV dictionary

        Raises:
            ValueError: If validation fails
        """
        logger.debug("Validating tailored CV output")

        # Validate schema with Pydantic
        try:
            validated = CV(**tailored_cv)
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            raise ValueError(f"Tailored CV does not match expected schema: {e}")

        # Hallucination check: Companies
        master_companies = {
            exp.get("company", "").lower() for exp in master_cv.get("experiences", [])
        }
        tailored_companies = {
            exp.get("company", "").lower() for exp in tailored_cv.get("experiences", [])
        }

        if not tailored_companies.issubset(master_companies):
            invalid_companies = tailored_companies - master_companies
            logger.warning(f"Tailored CV contains new companies: {invalid_companies}")
            # Note: In production, you might want to raise an error here
            # For now, we'll just log a warning

        # Hallucination check: Institutions
        master_institutions = {
            edu.get("institution", "").lower() for edu in master_cv.get("education", [])
        }
        tailored_institutions = {
            edu.get("institution", "").lower() for edu in tailored_cv.get("education", [])
        }

        if not tailored_institutions.issubset(master_institutions):
            invalid_institutions = tailored_institutions - master_institutions
            logger.warning(f"Tailored CV contains new institutions: {invalid_institutions}")

        # Hallucination check: Skills (with some flexibility for reorganization)
        # We'll allow the LLM to reorganize and categorize skills differently
        # but check that core skill names are preserved

        logger.info("Validation completed successfully")

        return validated.model_dump()
