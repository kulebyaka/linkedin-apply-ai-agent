"""Job posting data models"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ScrapedJob(BaseModel):
    """A job scraped from LinkedIn search results, optionally enriched with detail page data.

    Produced by LinkedInJobScraper and consumed by the job queue / preparation workflow.
    Card-level fields (job_id, title, company, location, url) are always present.
    Detail-level fields (description, salary_range, etc.) are populated after enrichment.
    """

    # Card-level fields (always present after scraping)
    job_id: str
    title: str
    company: str
    location: str
    url: str
    posted_date: Optional[datetime] = None
    easy_apply: bool = False

    # Detail-level fields (populated by scrape_job_details enrichment)
    description: str = ""
    requirements: Optional[str] = None
    salary_range: Optional[str] = None
    experience_level: Optional[str] = None
    job_type: Optional[str] = None


class JobPosting(BaseModel):
    """Model for a job posting"""
    id: str
    title: str
    company: str
    location: str
    description: str
    requirements: Optional[str] = None
    salary_range: Optional[str] = None
    is_remote: bool = False
    experience_level: Optional[str] = None
    job_type: Optional[str] = None  # full-time, part-time, contract
    posted_date: Optional[datetime] = None
    url: str
    raw_data: Optional[dict] = None
