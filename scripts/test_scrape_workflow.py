"""End-to-end test of the LinkedIn scrape + enrich pipeline.

Drives the production code path (LinkedInAutomation → LinkedInJobScraper →
scrape_and_enrich) with a small max_jobs, then prints a summary of each
scraped job including description length so we can verify the selector
fixes work against the live authenticated SDUI layout.

Requires:
- data/linkedin_cookies.json with a valid `li_at` cookie
- An SSH SOCKS tunnel running on 127.0.0.1:1080 to the VPS, or
  LINKEDIN_PROXY_SERVER set to whatever proxy the cookies were issued from.

Usage:
    LINKEDIN_PROXY_SERVER=socks5://127.0.0.1:1080 \
        uv run python scripts/test_scrape_workflow.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Settings
from src.services.linkedin.browser_automation import LinkedInAutomation
from src.services.linkedin.linkedin_scraper import LinkedInJobScraper
from src.services.linkedin.linkedin_search import LinkedInSearchParams


async def main() -> None:
    settings = Settings()
    settings.browser_headless = False  # easier to watch what's happening
    print(f"proxy: {settings.linkedin_proxy_server!r}")
    print(f"cookie file: {settings.linkedin_session_cookie_path}")

    browser = LinkedInAutomation(settings)
    await browser.initialize()
    try:
        # Load cookies WITHOUT /feed validation (the validation trips anti-bot).
        await browser.ensure_authenticated(validate_session=False)

        scraper = LinkedInJobScraper(browser, settings)
        params = LinkedInSearchParams(
            keywords=settings.linkedin_search_keywords or "python developer",
            location=settings.linkedin_search_location or "United States",
            max_jobs=3,
            easy_apply_only=False,
            date_posted=settings.linkedin_search_date_posted,
        )
        print(f"\n[search] keywords={params.keywords!r} location={params.location!r} "
              f"max_jobs={params.max_jobs}")

        jobs = await scraper.scrape_and_enrich(params)
        print(f"\n[result] scraped {len(jobs)} jobs")

        for i, j in enumerate(jobs, 1):
            desc = j.description or ""
            print(f"\n--- job {i}/{len(jobs)} ---")
            print(f"  id:          {j.job_id}")
            print(f"  title:       {j.title!r}")
            print(f"  company:     {j.company!r}")
            print(f"  location:    {j.location!r}")
            print(f"  url:         {j.url}")
            print(f"  desc_len:    {len(desc)}")
            if desc:
                print(f"  desc_preview: {desc[:200]!r}")
            else:
                print("  *** EMPTY DESCRIPTION ***")

        # Pass/fail summary
        with_desc = sum(1 for j in jobs if (j.description or "").strip())
        print(f"\n[summary] {with_desc}/{len(jobs)} jobs have non-empty descriptions")

    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
