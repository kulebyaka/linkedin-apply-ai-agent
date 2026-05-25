"""LinkedIn job scraper — search-results paging + per-job enrichment.

Detail-page parsing lives in :mod:`.detail_parser`; CSS selectors live in
:mod:`.selectors`; pure regex/string helpers live in :mod:`.parsing_utils`.
The constants and helpers are re-exported below so existing imports
``from src.services.linkedin.linkedin_scraper import SELECTORS, _parse_relative_time, ...``
continue to work.
"""

import logging

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.config.settings import Settings
from src.models.job import ScrapedJob

from .browser_automation import LinkedInAutomation
from .detail_parser import DetailPageParser
from .linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder
from .parsing_utils import (
    _extract_job_id_from_url,
    _extract_job_id_from_urn,
    _parse_relative_time,
)
from .selectors import (
    AUTHENTICATED_DESCRIPTION_SELECTORS,
    AUTHENTICATED_LAYOUT_MARKERS,
    GUEST_DESCRIPTION_SELECTOR,
    SELECTORS,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Public class
    "LinkedInJobScraper",
    # Re-exports for back-compat with existing imports/tests
    "SELECTORS",
    "AUTHENTICATED_DESCRIPTION_SELECTORS",
    "AUTHENTICATED_LAYOUT_MARKERS",
    "GUEST_DESCRIPTION_SELECTOR",
    "_parse_relative_time",
    "_extract_job_id_from_url",
    "_extract_job_id_from_urn",
]


class LinkedInJobScraper:
    """Scrapes LinkedIn job search results and orchestrates per-job enrichment."""

    # Class-level back-compat aliases. Tests reference these as
    # `LinkedInJobScraper.AUTHENTICATED_DESCRIPTION_SELECTORS` etc.
    AUTHENTICATED_DESCRIPTION_SELECTORS = AUTHENTICATED_DESCRIPTION_SELECTORS
    GUEST_DESCRIPTION_SELECTOR = GUEST_DESCRIPTION_SELECTOR
    AUTHENTICATED_LAYOUT_MARKERS = AUTHENTICATED_LAYOUT_MARKERS

    def __init__(self, browser: LinkedInAutomation, settings: Settings):
        self.browser = browser
        self.settings = settings
        self._seen_job_ids: set[str] = set()
        self._detail_parser = DetailPageParser(browser)

    def reset_seen(self) -> None:
        """Clear the set of seen job IDs for a new session."""
        self._seen_job_ids.clear()

    async def scrape_search_results(self, search_params: LinkedInSearchParams) -> list[ScrapedJob]:
        """Scrape job cards from LinkedIn search results pages.

        Paginates through results until max_jobs is reached or no more
        results are available. Skips already-seen job IDs (dedup).
        """
        all_jobs: list[ScrapedJob] = []
        page_num = 0
        max_jobs = search_params.max_jobs
        max_stale_pages = 3  # Stop after this many consecutive pages with no new jobs
        stale_pages = 0

        while len(all_jobs) < max_jobs:
            url = LinkedInSearchURLBuilder.build_url(search_params, page=page_num)
            logger.info("Navigating to search page %d: %s", page_num, url)

            await self.browser.page.goto(url, wait_until="domcontentloaded")

            # JSERP guest layout finishes rendering after domcontentloaded fires,
            # so wait for either a job card or the no-results banner before
            # counting. Without this the scraper races the render and silently
            # reports 0 jobs on cold loads.
            try:
                await self.browser.page.wait_for_selector(
                    f"{SELECTORS['job_card']}, {SELECTORS['no_results']}",
                    timeout=8000,
                )
            except PlaywrightTimeoutError:
                logger.warning(
                    "wait_for_selector timed out on page %d — page may be throttled or layout changed",
                    page_num,
                )

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
                title = await self.browser.page.title()
                final_url = self.browser.page.url
                content_snippet = (await self.browser.page.content())[:600]
                logger.info(
                    "No job cards found on page %d, stopping pagination "
                    "(title=%r, url=%s, head=%s)",
                    page_num,
                    title,
                    final_url,
                    content_snippet.replace("\n", " "),
                )
                break

            logger.info("Found %d job cards on page %d", card_count, page_num)

            jobs_before = len(all_jobs)

            for i in range(card_count):
                if len(all_jobs) >= max_jobs:
                    break

                card = cards.nth(i)
                job = await self._parse_job_card(card)

                if job is None:
                    logger.debug("Card %d on page %d returned None (parse failed)", i, page_num)
                    continue

                # Dedup
                if job.job_id in self._seen_job_ids:
                    logger.debug("Skipping duplicate job %s", job.job_id)
                    continue

                self._seen_job_ids.add(job.job_id)
                all_jobs.append(job)

            # Warn if cards were found but none could be parsed (possible selector breakage)
            parsed_this_page = len(all_jobs) - jobs_before
            if card_count > 0 and parsed_this_page == 0 and len(self._seen_job_ids) == 0:
                logger.warning(
                    "Page %d had %d cards but none were parsed — CSS selectors may be outdated",
                    page_num,
                    card_count,
                )

            # Track stale pages (cards found but no new jobs added)
            if len(all_jobs) == jobs_before:
                stale_pages += 1
                logger.info(
                    "No new jobs on page %d (%d/%d stale pages)",
                    page_num,
                    stale_pages,
                    max_stale_pages,
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

        logger.info("Scraped %d jobs total across %d pages", len(all_jobs), page_num + 1)
        return all_jobs

    async def scrape_job_details(self, job_url: str) -> dict:
        """Navigate to an individual job page and extract full details.

        Returns dict with description, requirements, salary, job_type,
        experience_level.
        """
        await self.browser.random_delay()
        await self.browser.page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
        await self.browser.random_delay(1.0, 3.0)

        return await self._parse_job_detail_page(self.browser.page)

    async def scrape_and_enrich(self, search_params: LinkedInSearchParams) -> list[ScrapedJob]:
        """Scrape search results then enrich each with full job details."""
        jobs = await self.scrape_search_results(search_params)
        enriched: list[ScrapedJob] = []

        for idx, job in enumerate(jobs, 1):
            if not job.url:
                logger.warning("No URL for job %s, skipping enrichment", job.job_id)
                enriched.append(job)
                continue

            logger.info("Enriching job %d/%d: %s (%s)", idx, len(jobs), job.job_id, job.title)
            try:
                details = await self.scrape_job_details(job.url)
                job = job.model_copy(update=details)
                logger.info(
                    "Enriched job %s: description=%d chars", job.job_id, len(job.description)
                )
            except Exception as exc:
                logger.warning("Failed to enrich job %s: %s", job.job_id, exc)

            enriched.append(job)

        return enriched

    async def _parse_job_card(self, card_element) -> ScrapedJob | None:
        """Extract data from a single job card DOM element.

        Returns a ScrapedJob with card-level fields populated, or None on failure.
        """
        try:
            # Job ID: try data-job-id (SPA), then data-entity-urn (guest), then URL.
            job_id = await card_element.get_attribute("data-job-id")
            if not job_id:
                job_id = _extract_job_id_from_urn(
                    await card_element.get_attribute("data-entity-urn")
                )

            # Title and URL from the link element. The SPA puts the title in
            # the anchor's aria-label; the guest layout has no aria-label but
            # exposes the title via an inner <span class="sr-only">.
            title_el = card_element.locator(SELECTORS["job_card_title"]).first
            title = await title_el.get_attribute("aria-label") or ""
            if not title:
                sr_only = title_el.locator("span.sr-only").first
                if await sr_only.count() > 0:
                    title = (await sr_only.text_content() or "").strip()
            if not title:
                title = (await title_el.text_content() or "").strip()
            title = " ".join(title.split())  # collapse whitespace
            if title.endswith(" with verification"):
                title = title[: -len(" with verification")]
            href = await title_el.get_attribute("href") or ""

            if not job_id:
                job_id = _extract_job_id_from_url(href)
            if not job_id:
                return None
            url = f"https://www.linkedin.com/jobs/view/{job_id}/"

            # Company
            company = ""
            if await card_element.locator(SELECTORS["job_card_company"]).count() > 0:
                company = " ".join(
                    (
                        await card_element.locator(
                            SELECTORS["job_card_company"]
                        ).first.text_content()
                        or ""
                    ).split()
                )

            # Location
            location = ""
            if await card_element.locator(SELECTORS["job_card_location"]).count() > 0:
                location = " ".join(
                    (
                        await card_element.locator(
                            SELECTORS["job_card_location"]
                        ).first.text_content()
                        or ""
                    ).split()
                )

            # Easy Apply badge
            easy_apply_count = await card_element.locator(SELECTORS["job_card_easy_apply"]).count()
            easy_apply = easy_apply_count > 0

            # Posted date (optional — element may not exist on all cards)
            posted_date = None
            posted_count = await card_element.locator(SELECTORS["job_card_posted"]).count()
            if posted_count > 0:
                posted_text = (
                    await card_element.locator(SELECTORS["job_card_posted"]).first.text_content()
                    or ""
                ).strip()
                posted_date = _parse_relative_time(posted_text)

            return ScrapedJob(
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                posted_date=posted_date,
                easy_apply=easy_apply,
                url=url,
            )
        except Exception as exc:
            logger.debug("Failed to parse job card: %s", exc)
            return None

    async def _parse_job_detail_page(self, page) -> dict:
        """Delegate to the layout-aware detail parser (kept for test compat)."""
        return await self._detail_parser.parse(page)
