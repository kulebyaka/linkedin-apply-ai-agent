"""Service for filtering job postings using LLM"""

from typing import Dict
from src.llm.provider import BaseLLMClient


class JobFilter:
    """Filters job postings using LLM to verify they match criteria"""

    def __init__(self, llm_client: BaseLLMClient):
        self.llm = llm_client

    def is_suitable(self, job_posting: Dict, filters: Dict) -> tuple[bool, str]:
        """
        Evaluate if a job posting matches the user's criteria

        Args:
            job_posting: Job posting data including description
            filters: User's filter criteria

        Returns:
            Tuple of (is_suitable: bool, reason: str)
        """
        prompt = self._build_filter_prompt(job_posting, filters)

        # TODO: Call LLM to evaluate job suitability
        # The LLM should check for hidden disqualifiers like:
        # - "remote" jobs that are actually on-site
        # - Required qualifications not mentioned in search
        # - Misleading job titles

        raise NotImplementedError

    def _build_filter_prompt(self, job_posting: Dict, filters: Dict) -> str:
        """Build the prompt for LLM job filtering"""
        return f"""
        Evaluate if this job posting matches the following criteria:

        Job Posting:
        Title: {job_posting.get('title')}
        Company: {job_posting.get('company')}
        Location: {job_posting.get('location')}
        Description: {job_posting.get('description')}

        Criteria:
        {filters}

        Carefully analyze the job description for:
        1. True remote work possibility (not hybrid or occasional remote)
        2. Required vs preferred qualifications
        3. Actual job responsibilities vs job title
        4. Any red flags or disqualifiers

        Return a JSON with:
        - suitable: boolean
        - reason: string explaining your decision
        - confidence: float (0-1)
        """
