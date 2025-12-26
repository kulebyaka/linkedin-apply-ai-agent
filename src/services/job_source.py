"""Job Source Adapters for extracting job postings from various sources.

This module provides an abstract interface and concrete adapters for:
- URL-based job extraction (lever.co, greenhouse.io, etc.)
- Manual job description input
- LinkedIn job postings (future)

IMPLEMENTATION STATUS: Interface/skeleton only - method bodies raise NotImplementedError.
Actual implementation will use HTTP + LLM structured output extraction.
"""

from abc import ABC, abstractmethod
from typing import Any

from ..models.job import JobPosting


class JobSourceAdapter(ABC):
    """Abstract base class for job source adapters.

    All job source adapters must implement the extract() method to convert
    source-specific input into a normalized JobPosting dict.
    """

    @abstractmethod
    async def extract(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        """Extract job posting from source-specific input.

        Args:
            raw_input: Source-specific input data. Structure depends on adapter:
                - URLJobExtractor: {"url": str}
                - ManualJobAdapter: {"title": str, "company": str, "description": str, ...}
                - LinkedInJobAdapter: {"job_id": str, "raw_data": dict}

        Returns:
            Normalized job posting dict matching JobPosting model structure:
            {
                "id": str,
                "title": str,
                "company": str,
                "location": str,
                "description": str,
                "requirements": str | None,
                "url": str,
                ...
            }

        Raises:
            NotImplementedError: If method is not implemented.
            JobExtractionError: If extraction fails.
        """
        pass

    @abstractmethod
    def can_handle(self, raw_input: dict[str, Any]) -> bool:
        """Check if this adapter can handle the given input.

        Args:
            raw_input: Source-specific input data.

        Returns:
            True if this adapter can process the input.
        """
        pass


class JobExtractionError(Exception):
    """Exception raised when job extraction fails."""

    def __init__(self, message: str, source: str, details: dict | None = None):
        self.message = message
        self.source = source
        self.details = details or {}
        super().__init__(self.message)


class URLJobExtractor(JobSourceAdapter):
    """Extract job postings from external URLs.

    Supports common job boards:
    - lever.co / jobs.lever.co
    - greenhouse.io / boards.greenhouse.io
    - workday
    - Generic URLs (fallback to LLM extraction)

    Implementation approach:
    1. HTTP fetch the job posting page
    2. Convert HTML to markdown (strip boilerplate)
    3. Use LLM with structured output to extract job details
    4. Return normalized JobPosting dict
    """

    SUPPORTED_DOMAINS = [
        "lever.co",
        "jobs.lever.co",
        "greenhouse.io",
        "boards.greenhouse.io",
        "myworkday.com",
        "myworkdayjobs.com",
    ]

    def __init__(self, llm_client: Any = None):
        """Initialize URL job extractor.

        Args:
            llm_client: LLM client for structured output extraction.
                        Will be used to parse job descriptions.
        """
        self.llm_client = llm_client

    async def extract(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        """Extract job posting from URL.

        Args:
            raw_input: {"url": str} - The job posting URL.

        Returns:
            Normalized job posting dict.

        Raises:
            NotImplementedError: Method not yet implemented.
        """
        raise NotImplementedError(
            "URL job extraction not yet implemented. "
            "Will use HTTP fetch + LLM structured output."
        )

    def can_handle(self, raw_input: dict[str, Any]) -> bool:
        """Check if input contains a URL."""
        return "url" in raw_input and isinstance(raw_input["url"], str)

    def _is_supported_domain(self, url: str) -> bool:
        """Check if URL is from a supported job board domain."""
        # TODO: Implement domain checking
        return True


class ManualJobAdapter(JobSourceAdapter):
    """Adapter for manually entered job descriptions.

    Simply normalizes and validates the manual input into a JobPosting dict.
    No external fetching required.
    """

    async def extract(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        """Normalize manual job description input.

        Args:
            raw_input: {
                "title": str,
                "company": str,
                "description": str,
                "requirements": str | None,
                "location": str | None,
                "url": str | None
            }

        Returns:
            Normalized job posting dict.

        Raises:
            NotImplementedError: Method not yet implemented.
        """
        raise NotImplementedError(
            "Manual job input processing not yet implemented. "
            "Will normalize and validate input fields."
        )

    def can_handle(self, raw_input: dict[str, Any]) -> bool:
        """Check if input contains manual job description fields."""
        required_fields = {"title", "company", "description"}
        return required_fields.issubset(raw_input.keys())


class LinkedInJobAdapter(JobSourceAdapter):
    """Adapter for LinkedIn job postings.

    Used by the LinkedIn cron job integration (future feature).
    Extracts job details from LinkedIn's job posting data structure.
    """

    async def extract(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        """Extract job posting from LinkedIn data.

        Args:
            raw_input: {"job_id": str, "raw_data": dict} - LinkedIn job data.

        Returns:
            Normalized job posting dict.

        Raises:
            NotImplementedError: Method not yet implemented.
        """
        raise NotImplementedError(
            "LinkedIn job extraction not yet implemented. "
            "Future feature for cron job integration."
        )

    def can_handle(self, raw_input: dict[str, Any]) -> bool:
        """Check if input contains LinkedIn job data."""
        return "job_id" in raw_input and "raw_data" in raw_input


class JobSourceFactory:
    """Factory for creating appropriate job source adapters.

    Usage:
        factory = JobSourceFactory(llm_client=llm)
        adapter = factory.get_adapter(source_type)
        job_data = await adapter.extract(raw_input)
    """

    def __init__(self, llm_client: Any = None):
        """Initialize factory with shared LLM client.

        Args:
            llm_client: LLM client for adapters that need it.
        """
        self.llm_client = llm_client
        self._adapters = {
            "url": URLJobExtractor(llm_client=llm_client),
            "manual": ManualJobAdapter(),
            "linkedin": LinkedInJobAdapter(),
        }

    def get_adapter(self, source_type: str) -> JobSourceAdapter:
        """Get adapter for the given source type.

        Args:
            source_type: One of "url", "manual", "linkedin".

        Returns:
            Appropriate JobSourceAdapter instance.

        Raises:
            ValueError: If source_type is not recognized.
        """
        if source_type not in self._adapters:
            raise ValueError(
                f"Unknown source type: {source_type}. "
                f"Supported: {list(self._adapters.keys())}"
            )
        return self._adapters[source_type]

    def get_adapter_for_input(self, raw_input: dict[str, Any]) -> JobSourceAdapter:
        """Auto-detect and return appropriate adapter for input.

        Args:
            raw_input: Source-specific input data.

        Returns:
            Adapter that can handle the input.

        Raises:
            ValueError: If no adapter can handle the input.
        """
        for adapter in self._adapters.values():
            if adapter.can_handle(raw_input):
                return adapter
        raise ValueError(
            f"No adapter can handle input with keys: {list(raw_input.keys())}"
        )
