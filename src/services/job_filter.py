"""Service for filtering job postings using LLM"""

from typing import Dict
from src.llm.provider import BaseLLMClient


class JobFilter:
    """Filters job postings using LLM to verify they match criteria"""

    # JSON Schema for structured output from LLM
    FILTER_RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "suitable": {
                "type": "boolean",
                "description": "Whether the job posting matches the user's criteria"
            },
            "reason": {
                "type": "string",
                "description": "Detailed explanation for the suitability decision"
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score between 0 and 1",
                "minimum": 0,
                "maximum": 1
            },
            "red_flags": {
                "type": "array",
                "description": "List of identified red flags or concerns",
                "items": {"type": "string"}
            }
        },
        "required": ["suitable", "reason", "confidence", "red_flags"]
    }

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

        # TODO: Implement LLM call with structured output
        # Example implementation:
        #
        # result = self.llm.generate_json(
        #     prompt=prompt,
        #     schema=self.FILTER_RESPONSE_SCHEMA,  # ← CRITICAL: Always pass schema
        #     temperature=0.3,  # Low temperature for precise evaluation
        #     max_retries=3
        # )
        #
        # return (result["suitable"], result["reason"])
        #
        # The LLM will check for hidden disqualifiers like:
        # - "remote" jobs that are actually on-site
        # - Required qualifications not mentioned in search
        # - Misleading job titles
        # - Red flags in job description

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

        Provide your evaluation with:
        - Whether the job is suitable based on the criteria
        - A detailed explanation for your decision
        - Your confidence level (0-1) in this assessment
        - A list of any red flags or concerns you identified
        """.strip()
