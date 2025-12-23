"""Prompt management for CV composition"""

from pathlib import Path
from typing import Dict, Optional
from string import Template
import logging

logger = logging.getLogger(__name__)


class PromptLoader:
    """Loads and manages prompts from external files"""

    def __init__(self, prompts_dir: str | Path = "prompts/cv_composer"):
        """
        Initialize prompt loader

        Args:
            prompts_dir: Directory containing prompt files
        """
        self.prompts_dir = Path(prompts_dir)
        self._cache: Dict[str, str] = {}
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
                f"Prompt file not found: {prompt_file}\n"
                f"Available prompts: {self.list_available()}"
            )

        prompt_content = prompt_file.read_text(encoding="utf-8")
        self._cache[prompt_name] = prompt_content

        logger.debug(f"Loaded prompt: {prompt_name}")
        return prompt_content

    def reload(self, prompt_name: Optional[str] = None):
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
        return [
            f.stem for f in self.prompts_dir.glob("*.txt")
            if f.is_file()
        ]

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


class CVPromptManager:
    """High-level prompt management for CV composition"""

    def __init__(self, prompts_dir: Optional[str | Path] = None):
        """
        Initialize CV prompt manager

        Args:
            prompts_dir: Custom prompts directory (defaults to prompts/cv_composer)
        """
        self.loader = PromptLoader(prompts_dir or "prompts/cv_composer")

    def get_job_summary_prompt(self, job_description: str) -> str:
        """Get prompt for job description summarization"""
        return self.loader.get_template(
            "job_summary",
            job_description=job_description
        )

    def get_summary_prompt(
        self,
        current_role: str,
        years_experience: int,
        key_skills: list[str],
        achievements: list[str],
        job_summary: Dict
    ) -> str:
        """Get prompt for professional summary generation"""
        import json
        return self.loader.get_template(
            "summary",
            current_role=current_role,
            years_experience=years_experience,
            key_skills=", ".join(key_skills),
            achievements="\n".join(f"- {a}" for a in achievements),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_experience_prompt(
        self,
        experiences: list[Dict],
        job_summary: Dict
    ) -> str:
        """Get prompt for experience section tailoring"""
        import json
        return self.loader.get_template(
            "experience",
            experiences=json.dumps(experiences, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_education_prompt(self, education: list[Dict], job_summary: Dict) -> str:
        """Get prompt for education section"""
        import json
        return self.loader.get_template(
            "education",
            education=json.dumps(education, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_skills_prompt(self, skills: list[Dict], job_summary: Dict) -> str:
        """Get prompt for skills optimization"""
        import json
        return self.loader.get_template(
            "skills",
            skills=json.dumps(skills, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_projects_prompt(self, projects: list[Dict], job_summary: Dict) -> str:
        """Get prompt for projects highlighting"""
        import json
        return self.loader.get_template(
            "projects",
            projects=json.dumps(projects, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_certifications_prompt(
        self,
        certifications: list[str],
        job_summary: Dict
    ) -> str:
        """Get prompt for certifications display"""
        import json
        return self.loader.get_template(
            "certifications",
            certifications=json.dumps(certifications, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )

    def get_full_cv_prompt(self, master_cv: Dict, job_summary: Dict) -> str:
        """
        Get prompt for generating complete tailored CV in a single LLM call.

        This combines all section prompts into one unified prompt for better
        performance (reduces 6 LLM calls to 1).

        Args:
            master_cv: Complete master CV data
            job_summary: Structured job requirements from job analysis

        Returns:
            Formatted prompt for full CV generation
        """
        import json
        return self.loader.get_template(
            "full_cv",
            master_cv=json.dumps(master_cv, indent=2),
            job_summary=json.dumps(job_summary, indent=2)
        )
