"""Service for composing tailored CV using LLM"""

from typing import Dict
from src.llm.provider import BaseLLMClient


class CVComposer:
	"""Composes tailored CV based on job description using LLM"""

	def __init__(self, llm_client: BaseLLMClient):
		self.llm = llm_client

	def compose_cv(
		self,
		master_cv: Dict,
		job_posting: Dict,
		user_feedback: str | None = None
	) -> Dict:
		"""
		Generate a tailored CV for a specific job posting

		Args:
			master_cv: Complete CV data in JSON format
			job_posting: Job posting information
			user_feedback: Optional feedback from user for retry

		Returns:
			Tailored CV as JSON
		"""
		prompt = self._build_cv_prompt(master_cv, job_posting, user_feedback)

		# TODO: Call LLM to generate tailored CV JSON
		# The LLM should:
		# 1. Reorder experiences to highlight relevant ones
		# 2. Emphasize relevant skills
		# 3. Adjust summary/objective for the role
		# 4. Keep factual accuracy (no hallucinations)

		raise NotImplementedError

	def _build_cv_prompt(
		self,
		master_cv: Dict,
		job_posting: Dict,
		user_feedback: str | None
	) -> str:
		"""Build the prompt for CV composition"""
		base_prompt = f"""
        Create a tailored CV for the following job posting.

        Master CV Data:
        {master_cv}

        Job Posting:
        Title: {job_posting.get('title')}
        Company: {job_posting.get('company')}
        Description: {job_posting.get('description')}
        Requirements: {job_posting.get('requirements')}

        Instructions:
        1. Reorganize experiences to highlight relevant ones first
        2. Emphasize skills that match the job requirements
        3. Tailor the professional summary to the role
        4. Keep all information factual - do not add fake experiences
        5. Maintain the same JSON schema as the master CV

        Return the tailored CV as valid JSON.
        """

		if user_feedback:
			base_prompt += f"\n\nUser Feedback for Revision:\n{user_feedback}"

		return base_prompt
