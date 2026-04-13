"""LinkedIn scraping and browser automation services.

Note: LinkedInAutomation and LinkedInJobScraper are NOT re-exported here
because they eagerly import Playwright (heavy browser dependency). Import
them directly:
    from src.services.linkedin.browser_automation import LinkedInAutomation
    from src.services.linkedin.linkedin_scraper import LinkedInJobScraper
"""

from .linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder

__all__ = [
    "LinkedInSearchParams",
    "LinkedInSearchURLBuilder",
]
