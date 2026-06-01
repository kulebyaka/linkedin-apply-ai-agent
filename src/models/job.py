"""Job posting data models"""

from datetime import datetime

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
    posted_date: datetime | None = None
    easy_apply: bool = False

    # Detail-level fields (populated by scrape_job_details enrichment)
    description: str = ""
    requirements: str | None = None
    salary_range: str | None = None
    experience_level: str | None = None
    job_type: str | None = None

    # Observability: was LinkedIn serving us the authenticated layout when this
    # job's detail page was scraped? True = authenticated SDUI/SPA layout,
    # False = guest/authwall layout, None = not enriched / detection unavailable.
    # Set from the detail-page layout detection; surfaces stale-cookie state.
    session_authenticated: bool | None = None
