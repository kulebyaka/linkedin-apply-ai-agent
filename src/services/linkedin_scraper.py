"""LinkedIn job scraper — parses search results and job detail pages.

Uses LinkedInAutomation for browser interactions and LinkedInSearchURLBuilder
for constructing search URLs. Supports pagination, dedup, and enrichment
of job cards with full detail page data.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.services.browser_automation import LinkedInAutomation
from src.services.linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder

logger = logging.getLogger(__name__)

# CSS selectors for LinkedIn job search results and detail pages.
# These target the current LinkedIn DOM structure and may need updating
# if LinkedIn changes their markup.
SELECTORS = {
    "job_card": "div.job-card-container, li.jobs-search-results__list-item",
    "job_card_title": "a.job-card-list__title, a.job-card-container__link",
    "job_card_company": "span.job-card-container__primary-description, span.artdeco-entity-lockup__subtitle",
    "job_card_location": "li.job-card-container__metadata-item, span.artdeco-entity-lockup__caption",
    "job_card_easy_apply": "li-icon[type='linkedin-bug'], span.job-card-container__apply-method",
    "job_card_posted": "time, span.job-card-container__listed-time",
    "detail_description": "div.jobs-description__content, div#job-details",
    "detail_criteria": "li.jobs-unified-top-card__job-insight, ul.job-criteria__list li",
    "detail_salary": "div.salary-main-rail__data-body, span.jobs-unified-top-card__salary",
    "no_results": "div.jobs-search-no-results-banner",
}

# Relative time patterns used by LinkedIn (e.g. "2 days ago", "1 week ago")
_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago", re.IGNORECASE
)

_TIME_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
}


def _parse_relative_time(text: str) -> datetime | None:
    """Parse LinkedIn relative time strings like '2 days ago' into datetime."""
    match = _RELATIVE_TIME_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = _TIME_UNIT_SECONDS.get(unit, 0) * amount
    return datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)


def _extract_job_id_from_url(url: str) -> str | None:
    """Extract LinkedIn job ID from a job URL or href.

    LinkedIn job URLs typically contain /view/<job_id>/ or ?currentJobId=<id>.
    """
    # Pattern: /jobs/view/1234567890/
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)
    # Pattern: currentJobId=1234567890
    m = re.search(r"currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    return None


class LinkedInJobScraper:
    """Scrapes LinkedIn job search results and detail pages."""

    def __init__(self, browser: LinkedInAutomation, settings: Settings):
        self.browser = browser
        self.settings = settings
        self._seen_job_ids: set[str] = set()

    def reset_seen(self) -> None:
        """Clear the set of seen job IDs for a new session."""
        self._seen_job_ids.clear()

    async def scrape_search_results(
        self, search_params: LinkedInSearchParams
    ) -> list[dict]:
        """Scrape job cards from LinkedIn search results pages.

        Paginates through results until max_jobs is reached or no more
        results are available. Skips already-seen job IDs (dedup).

        Returns list of raw job dicts extracted from job cards.
        """
        all_jobs: list[dict] = []
        page_num = 0
        max_jobs = search_params.max_jobs
        max_stale_pages = 3  # Stop after this many consecutive pages with no new jobs
        stale_pages = 0

        while len(all_jobs) < max_jobs:
            url = LinkedInSearchURLBuilder.build_url(search_params, page=page_num)
            logger.info("Navigating to search page %d: %s", page_num, url)

            await self.browser.page.goto(url, wait_until="domcontentloaded")
            await self.browser.random_delay()
            await self.browser.human_scroll(self.browser.page)

            # Check for no results
            no_results = await self.browser.page.locator(SELECTORS["no_results"]).count()
            if no_results > 0:
                logger.info("No results found on page %d", page_num)
                break

            # Parse job cards on current page
            cards = self.browser.page.locator(SELECTORS["job_card"])
            card_count = await cards.count()

            if card_count == 0:
                logger.info("No job cards found on page %d, stopping pagination", page_num)
                break

            logger.info("Found %d job cards on page %d", card_count, page_num)

            jobs_before = len(all_jobs)

            for i in range(card_count):
                if len(all_jobs) >= max_jobs:
                    break

                card = cards.nth(i)
                job_data = await self._parse_job_card(card)

                if not job_data or not job_data.get("job_id"):
                    continue

                # Dedup
                if job_data["job_id"] in self._seen_job_ids:
                    logger.debug("Skipping duplicate job %s", job_data["job_id"])
                    continue

                self._seen_job_ids.add(job_data["job_id"])
                all_jobs.append(job_data)

            # Track stale pages (cards found but no new jobs added)
            if len(all_jobs) == jobs_before:
                stale_pages += 1
                logger.info(
                    "No new jobs on page %d (%d/%d stale pages)",
                    page_num, stale_pages, max_stale_pages,
                )
                if stale_pages >= max_stale_pages:
                    logger.info("Stopping pagination: %d consecutive stale pages", stale_pages)
                    break
            else:
                stale_pages = 0

            page_num += 1

            # Delay between pages
            await self.browser.random_delay(
                self.browser.page_delay_min, self.browser.page_delay_max
            )

        logger.info("Scraped %d jobs total across %d pages", len(all_jobs), page_num)
        return all_jobs

    async def scrape_job_details(self, job_url: str) -> dict:
        """Navigate to an individual job page and extract full details.

        Returns dict with description, requirements, salary, job_type,
        experience_level.
        """
        await self.browser.random_delay()
        await self.browser.page.goto(job_url, wait_until="domcontentloaded")
        await self.browser.random_delay(1.0, 3.0)

        return await self._parse_job_detail_page(self.browser.page)

    async def scrape_and_enrich(
        self, search_params: LinkedInSearchParams
    ) -> list[dict]:
        """Scrape search results then enrich each with full job details.

        Returns list of enriched job dicts matching JobPosting field structure.
        """
        jobs = await self.scrape_search_results(search_params)
        enriched: list[dict] = []

        for job in jobs:
            job_url = job.get("url")
            if not job_url:
                logger.warning("No URL for job %s, skipping enrichment", job.get("job_id"))
                enriched.append(job)
                continue

            try:
                details = await self.scrape_job_details(job_url)
                job.update(details)
            except Exception as exc:
                logger.warning("Failed to enrich job %s: %s", job.get("job_id"), exc)

            enriched.append(job)

        return enriched

    async def _parse_job_card(self, card_element) -> dict | None:
        """Extract data from a single job card DOM element.

        Returns dict with keys: job_id, title, company, location,
        posted_date, easy_apply, url.
        """
        try:
            # Title and URL from the link element
            title_el = card_element.locator(SELECTORS["job_card_title"]).first
            title = (await title_el.text_content() or "").strip()
            href = await title_el.get_attribute("href") or ""

            job_id = _extract_job_id_from_url(href)
            url = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else href

            # Company
            company_el = card_element.locator(SELECTORS["job_card_company"]).first
            company = (await company_el.text_content() or "").strip()

            # Location
            location_el = card_element.locator(SELECTORS["job_card_location"]).first
            location = (await location_el.text_content() or "").strip()

            # Easy Apply badge
            easy_apply_count = await card_element.locator(
                SELECTORS["job_card_easy_apply"]
            ).count()
            easy_apply = easy_apply_count > 0

            # Posted date
            posted_el = card_element.locator(SELECTORS["job_card_posted"]).first
            posted_text = (await posted_el.text_content() or "").strip()
            posted_date = _parse_relative_time(posted_text)

            return {
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "posted_date": posted_date,
                "easy_apply": easy_apply,
                "url": url,
            }
        except Exception as exc:
            logger.debug("Failed to parse job card: %s", exc)
            return None

    async def _parse_job_detail_page(self, page) -> dict:
        """Extract full details from a job detail page.

        Returns dict with keys: description, requirements, salary,
        job_type, experience_level.
        """
        result: dict = {
            "description": "",
            "requirements": None,
            "salary_range": None,
            "job_type": None,
            "experience_level": None,
        }

        try:
            desc_el = page.locator(SELECTORS["detail_description"]).first
            desc_count = await page.locator(SELECTORS["detail_description"]).count()
            if desc_count > 0:
                result["description"] = (await desc_el.text_content() or "").strip()
        except Exception as exc:
            logger.debug("Failed to extract description: %s", exc)

        # Extract job criteria (experience level, job type, etc.)
        try:
            criteria_items = page.locator(SELECTORS["detail_criteria"])
            criteria_count = await criteria_items.count()
            for i in range(criteria_count):
                text = (await criteria_items.nth(i).text_content() or "").strip().lower()
                if any(
                    kw in text
                    for kw in ("entry", "associate", "mid-senior", "director", "executive")
                ):
                    result["experience_level"] = text.strip()
                elif any(
                    kw in text
                    for kw in ("full-time", "part-time", "contract", "temporary", "internship")
                ):
                    result["job_type"] = text.strip()
        except Exception as exc:
            logger.debug("Failed to extract criteria: %s", exc)

        # Salary
        try:
            salary_count = await page.locator(SELECTORS["detail_salary"]).count()
            if salary_count > 0:
                salary_el = page.locator(SELECTORS["detail_salary"]).first
                result["salary_range"] = (await salary_el.text_content() or "").strip()
        except Exception as exc:
            logger.debug("Failed to extract salary: %s", exc)

        return result
