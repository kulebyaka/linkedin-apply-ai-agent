"""Prompt management for CV composition"""

import logging
from pathlib import Path
from string import Template

from src.llm.prompt_spec import PromptSpec

logger = logging.getLogger(__name__)


CV_EXTRACTION_PROMPT = """You are extracting a structured résumé from a PDF document.

Read the attached PDF and produce a JSON object matching the CV schema you have been instructed to follow.

Rules:
- Use ONLY information present in the PDF. Do not invent companies, dates, achievements, or contact details. If a field is missing, use null (for nullable fields) or an empty list.
- Dates MUST be ISO 8601 strings: "YYYY-MM-DD". If the source only gives a month and year (e.g. "Sep 2020"), use the first of the month ("2020-09-01"). If only the year is known, use "YYYY-01-01". For ongoing positions/education, set end_date to null and is_current to true.
- Email must be a valid address. If no email is present, use "unknown@example.com" so the structure validates — the user will correct it.
- Group skills into sensible categories (e.g. "Programming Languages", "Frameworks", "Cloud", "Tools").
- Keep descriptions and achievements concise — quote the résumé wording, do not paraphrase aggressively.
- Preserve the original ordering of experiences and education (most recent first if that is how the résumé is laid out).
- If the résumé lists projects separately from work experience, place them under "projects". If they are listed within a job, attach them to that experience's "projects" field.
- For languages, return objects {"language": ..., "level": ...}. If proficiency is not stated, use "Conversational".
- Return ONLY the JSON object — no commentary, no markdown fences.
"""


class PromptLoader:
    """Loads and manages prompts from external files"""

    def __init__(self, prompts_dir: str | Path = "prompts/cv_composer"):
        """
        Initialize prompt loader

        Args:
            prompts_dir: Directory containing prompt files
        """
        self.prompts_dir = Path(prompts_dir)
        self._cache: dict[str, str] = {}
        self._ensure_prompts_exist()

    def _ensure_prompts_exist(self):
        """Ensure prompts directory exists, copy from examples if needed"""
        if not self.prompts_dir.exists():
            self.prompts_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created prompts directory: {self.prompts_dir}")

        # Copy example prompts if user prompts don't exist
        examples_dir = self.prompts_dir / "examples"
        if examples_dir.exists():
            for example_file in examples_dir.glob("*.txt"):
                target_file = self.prompts_dir / example_file.name
                if not target_file.exists():
                    target_file.write_text(example_file.read_text(encoding="utf-8"))
                    logger.info(f"Copied example prompt: {example_file.name}")

    def load(self, prompt_name: str, use_cache: bool = True) -> str:
        """
        Load a prompt by name

        Args:
            prompt_name: Name of prompt (without .txt extension)
            use_cache: Whether to use cached version

        Returns:
            Prompt template string

        Raises:
            FileNotFoundError: If prompt file doesn't exist
        """
        if use_cache and prompt_name in self._cache:
            return self._cache[prompt_name]

        prompt_file = self.prompts_dir / f"{prompt_name}.txt"

        if not prompt_file.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_file}\nAvailable prompts: {self.list_available()}"
            )

        prompt_content = prompt_file.read_text(encoding="utf-8")
        self._cache[prompt_name] = prompt_content

        logger.debug(f"Loaded prompt: {prompt_name}")
        return prompt_content

    def reload(self, prompt_name: str | None = None):
        """
        Reload prompts from disk (useful for hot-reload during development)

        Args:
            prompt_name: Specific prompt to reload, or None to reload all
        """
        if prompt_name:
            self._cache.pop(prompt_name, None)
            logger.info(f"Reloaded prompt: {prompt_name}")
        else:
            self._cache.clear()
            logger.info("Reloaded all prompts")

    def list_available(self) -> list[str]:
        """List all available prompt names"""
        return [f.stem for f in self.prompts_dir.glob("*.txt") if f.is_file()]

    def get_template(self, prompt_name: str, **kwargs) -> str:
        """
        Load prompt and substitute variables

        Args:
            prompt_name: Name of prompt
            **kwargs: Variables to substitute in template

        Returns:
            Formatted prompt string
        """
        prompt = self.load(prompt_name)
        template = Template(prompt)

        try:
            return template.safe_substitute(**kwargs)
        except KeyError as e:
            logger.error(f"Missing template variable in {prompt_name}: {e}")
            raise

    def load_spec(
        self,
        prompt_name: str,
        *,
        cache_key: str,
        system_vars: dict | None = None,
        user_vars: dict | None = None,
    ) -> PromptSpec:
        """Load a split prompt template (``<name>.system.txt`` + ``<name>.user.txt``)
        and substitute variables into each side.

        Returns a ``PromptSpec`` ready to hand to ``BaseLLMClient.generate_json``.
        """
        system_template = self._read_file(f"{prompt_name}.system.txt")
        user_template = self._read_file(f"{prompt_name}.user.txt")

        try:
            system = Template(system_template).safe_substitute(**(system_vars or {}))
            user = Template(user_template).safe_substitute(**(user_vars or {}))
        except KeyError as e:
            logger.error(f"Missing template variable in {prompt_name} spec: {e}")
            raise

        return PromptSpec(system=system, user=user, cache_key=cache_key)

    def _read_file(self, filename: str) -> str:
        path = self.prompts_dir / filename
        if filename in self._cache:
            return self._cache[filename]
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {path}\nAvailable prompts: {self.list_available()}"
            )
        content = path.read_text(encoding="utf-8")
        self._cache[filename] = content
        return content


class CVPromptManager:
    """High-level prompt management for CV composition"""

    def __init__(self, prompts_dir: str | Path | None = None):
        """
        Initialize CV prompt manager

        Args:
            prompts_dir: Custom prompts directory (defaults to prompts/cv_composer)
        """
        self.loader = PromptLoader(prompts_dir or "prompts/cv_composer")

    def get_job_summary_spec(
        self, *, job_description: str, cache_key: str
    ) -> PromptSpec:
        """Cache-aware spec for job description summarization.

        Static block: instructions + JSON output schema.
        Variable block: the actual job description.
        """
        return self.loader.load_spec(
            "job_summary",
            cache_key=cache_key,
            user_vars={"job_description": job_description},
        )

    def get_summary_prompt(
        self,
        current_role: str,
        years_experience: int,
        key_skills: list[str],
        achievements: list[str],
        job_summary: dict,
    ) -> str:
        """Get prompt for professional summary generation"""
        import json

        return self.loader.get_template(
            "summary",
            current_role=current_role,
            years_experience=years_experience,
            key_skills=", ".join(key_skills),
            achievements="\n".join(f"- {a}" for a in achievements),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_experience_prompt(self, experiences: list[dict], job_summary: dict) -> str:
        """Get prompt for experience section tailoring"""
        import json

        return self.loader.get_template(
            "experience",
            experiences=json.dumps(experiences, indent=2),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_education_prompt(self, education: list[dict], job_summary: dict) -> str:
        """Get prompt for education section"""
        import json

        return self.loader.get_template(
            "education",
            education=json.dumps(education, indent=2),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_skills_prompt(self, skills: list[dict], job_summary: dict) -> str:
        """Get prompt for skills optimization"""
        import json

        return self.loader.get_template(
            "skills",
            skills=json.dumps(skills, indent=2),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_projects_prompt(self, projects: list[dict], job_summary: dict) -> str:
        """Get prompt for projects highlighting"""
        import json

        return self.loader.get_template(
            "projects",
            projects=json.dumps(projects, indent=2),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_certifications_prompt(self, certifications: list[str], job_summary: dict) -> str:
        """Get prompt for certifications display"""
        import json

        return self.loader.get_template(
            "certifications",
            certifications=json.dumps(certifications, indent=2),
            job_summary=json.dumps(job_summary, indent=2),
        )

    def get_full_cv_spec(
        self,
        *,
        master_cv: dict,
        job_summary: dict,
        user_feedback: str | None = None,
        cache_key: str,
    ) -> PromptSpec:
        """Cache-aware spec for full CV composition.

        Static block (cached per user): instructions + JSON output schema +
        master CV. Variable block: job_summary + optional user_feedback section.
        """
        import json

        user_feedback_section = ""
        if user_feedback:
            user_feedback_section = (
                "## User Feedback (IMPORTANT - Address these points):\n"
                f"{user_feedback}\n\n"
                "Please regenerate the CV addressing the feedback above while "
                "maintaining all other requirements."
            )

        return self.loader.load_spec(
            "full_cv",
            cache_key=cache_key,
            system_vars={"master_cv": json.dumps(master_cv, indent=2)},
            user_vars={
                "job_summary": json.dumps(job_summary, indent=2),
                "user_feedback_section": user_feedback_section,
            },
        )
