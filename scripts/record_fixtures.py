#!/usr/bin/env python
"""One-shot script: scrape LinkedIn jobs and save to fixture file.

Usage:
    python scripts/record_fixtures.py

Uses settings from .env (keywords, location, filters, etc.).
Saves scraped jobs to SCRAPED_JOBS_PATH (default: data/jobs/scraped_jobs.json).
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import get_settings
from src.services.browser_automation import LinkedInAutomation
from src.services.job_fixtures import save_scraped_jobs
from src.services.linkedin_scraper import LinkedInJobScraper
from src.services.linkedin_search import LinkedInSearchParams


async def main():
    settings = get_settings()

    print(f"Initializing browser (headless={settings.browser_headless})...")
    browser = LinkedInAutomation(settings)
    await browser.initialize()

    try:
        print("Authenticating with LinkedIn...")
        await browser.ensure_authenticated()

        params = LinkedInSearchParams(
            keywords=settings.linkedin_search_keywords,
            location=settings.linkedin_search_location,
            remote_filter=settings.linkedin_search_remote_filter,
            date_posted=settings.linkedin_search_date_posted,
            experience_level=settings.linkedin_search_experience_level,
            job_type=settings.linkedin_search_job_type,
            easy_apply_only=settings.linkedin_search_easy_apply_only,
            max_jobs=settings.linkedin_search_max_jobs,
        )

        print(f"Scraping jobs: keywords='{params.keywords}', location='{params.location}', max={params.max_jobs}...")
        scraper = LinkedInJobScraper(browser, settings)
        jobs = await scraper.scrape_and_enrich(params)
        print(f"Scraped {len(jobs)} jobs")

        path = settings.scraped_jobs_path
        count = save_scraped_jobs(jobs, path)
        print(f"Saved {count} jobs to {path}")

    finally:
        await browser.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
