"""Smoke test for LinkedInJobScraper against live LinkedIn.

Loads saved cookies, runs the real LinkedInJobScraper.scrape_search_results
with a fixed search, and prints what gets parsed.

Run after `linkedin_login_headed.py` has produced data/linkedin_cookies.json.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Settings  # noqa: E402
from src.services.linkedin.browser_automation import LinkedInAutomation  # noqa: E402
from src.services.linkedin.linkedin_scraper import LinkedInJobScraper  # noqa: E402
from src.services.linkedin.linkedin_search import LinkedInSearchParams  # noqa: E402


async def main() -> int:
    settings = Settings()
    settings.browser_headless = True  # match production
    browser = LinkedInAutomation(settings)
    await browser.initialize()
    try:
        await browser.ensure_authenticated()
        scraper = LinkedInJobScraper(browser, settings)
        params = LinkedInSearchParams(
            keywords="ai developer",
            location="Prague, Czechia",
            date_posted="week",
            experience_level=["mid-senior", "director", "executive", "associate"],
            job_type=["full-time", "part-time", "contract", "temporary"],
            max_jobs=5,
        )
        jobs = await scraper.scrape_search_results(params)
        print(f"\nScraped {len(jobs)} jobs:\n")
        for j in jobs:
            print(f"  {j.job_id}  {j.title!r}")
            print(f"        company={j.company!r}  location={j.location!r}")
            print(f"        easy_apply={j.easy_apply}  posted={j.posted_date}")
            print(f"        url={j.url}\n")
    finally:
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
