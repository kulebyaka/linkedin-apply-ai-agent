"""LinkedIn job scraper — parses search results and job detail pages.

Uses LinkedInAutomation for browser interactions and LinkedInSearchURLBuilder
for constructing search URLs. Supports pagination, dedup, and enrichment
of job cards with full detail page data.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.config.settings import Settings
from src.models.job import ScrapedJob

from .browser_automation import LinkedInAutomation
from .linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder

logger = logging.getLogger(__name__)

# CSS selectors for LinkedIn job search results and detail pages.
# Two layouts coexist: the authenticated SPA ("job-card-container" family) and
# the public guest JSERP ("job-search-card"/"base-search-card" family). Direct
# deep-links to /jobs/search/?... often render the guest layout even with a
# valid session cookie, so each selector covers both.
SELECTORS = {
    "job_card": "div.job-card-container, div.job-search-card",
    "job_card_title": (
        "a.job-card-container__link, a.job-card-list__title--link, "
        "a.base-card__full-link, h3.base-search-card__title"
    ),
    "job_card_company": (
        "div.artdeco-entity-lockup__subtitle span, "
        "span.job-card-container__primary-description, "
        "h4.base-search-card__subtitle a, h4.base-search-card__subtitle"
    ),
    "job_card_location": (
        "div.artdeco-entity-lockup__caption li span, "
        "li.job-card-container__metadata-item, "
        "span.job-search-card__location"
    ),
    "job_card_easy_apply": (
        "li-icon[type='linkedin-bug'], span.job-card-container__apply-method"
    ),
    "job_card_posted": (
        "time, span.job-card-container__listed-time, time.job-search-card__listed-time"
    ),
    # Authenticated SDUI layout (current as of 2026-05): hashed CSS classes are
    # rotated, so we anchor on the stable `data-sdui-component` attribute.
    # Note: we target the section container, not the inner `[data-testid=
    # "expandable-text-box"]` — that span ends up empty in the parsed DOM
    # because LinkedIn's markup nests `<p>` inside `<span>` inside `<p>`, which
    # browsers auto-correct by hoisting the inner content out. The container's
    # innerText includes the "About the job" heading; strip it in code.
    "detail_description": (
        "[data-sdui-component$='aboutTheJob'], "
        "div.jobs-description__content, div#job-details, "
        "div.show-more-less-html__markup, div.description__text"
    ),
    "detail_criteria": (
        "li.jobs-unified-top-card__job-insight, ul.job-criteria__list li, "
        "ul.description__job-criteria-list li"
    ),
    "detail_salary": "div.salary-main-rail__data-body, span.jobs-unified-top-card__salary",
    "detail_show_more": (
        "[data-sdui-component$='aboutTheJob'] [data-testid='expandable-text-button'], "
        "button.jobs-description__footer-button, button[aria-label='Show more'], "
        "button.show-more-less-html__button--more, "
        "button:has-text('Show more')"
    ),
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

    Authenticated SPA: /jobs/view/<id>/ or ?currentJobId=<id>.
    Public guest JSERP: /jobs/view/<slug>-<id>?... (trailing digits after slug).
    """
    # Pattern: /jobs/view/1234567890/
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)
    # Pattern: currentJobId=1234567890
    m = re.search(r"currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    # Pattern: /jobs/view/<slug>-<id>(?|/|$) — guest layout
    m = re.search(r"/jobs/view/[^?/]*?-(\d+)(?=[?/]|$)", url)
    if m:
        return m.group(1)
    return None


def _extract_job_id_from_urn(urn: str | None) -> str | None:
    """Extract LinkedIn job ID from `urn:li:jobPosting:<id>` (guest layout)."""
    if not urn:
        return None
    m = re.search(r"urn:li:jobPosting:(\d+)", urn)
    return m.group(1) if m else None


class LinkedInJobScraper:
    """Scrapes LinkedIn job search results and detail pages."""

    def __init__(self, browser: LinkedInAutomation, settings: Settings):
        self.browser = browser
        self.settings = settings
        self._seen_job_ids: set[str] = set()

    def reset_seen(self) -> None:
        """Clear the set of seen job IDs for a new session."""
        self._seen_job_ids.clear()

    async def scrape_search_results(self, search_params: LinkedInSearchParams) -> list[ScrapedJob]:
        """Scrape job cards from LinkedIn search results pages.

        Paginates through results until max_jobs is reached or no more
        results are available. Skips already-seen job IDs (dedup).

        Returns list of ScrapedJob instances extracted from job cards.
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
        """Scrape search results then enrich each with full job details.

        Returns list of ScrapedJob instances with detail-level fields populated.
        """
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

    # Authenticated (SDUI / legacy SPA) description container selectors,
    # tried in priority order.
    AUTHENTICATED_DESCRIPTION_SELECTORS = (
        "[data-sdui-component$='aboutTheJob']",
        "div.jobs-description__content",
        "div#job-details",
    )

    # Guest (JSERP) description selector. Only the inner markup div — never
    # the outer `div.description__text` wrapper, which contains the
    # "Show more"/"Show less" toggle buttons as siblings of the markup.
    GUEST_DESCRIPTION_SELECTOR = "div.show-more-less-html__markup"

    # Markers used to detect which layout LinkedIn served. Authenticated
    # markers are checked first; absence implies guest. (We don't trust
    # `li_at` cookie presence alone — sessions can be silently downgraded.)
    AUTHENTICATED_LAYOUT_MARKERS = (
        "[data-sdui-component$='aboutTheJob']",
        "div.jobs-description__content",
        "div#job-details",
        "h2:has-text('About the job')",
    )

    async def _detect_layout(self, page) -> str:
        """Return 'authenticated' if SDUI/SPA markers are present, else 'guest'."""
        for marker in self.AUTHENTICATED_LAYOUT_MARKERS:
            try:
                if await page.locator(marker).count() > 0:
                    return "authenticated"
            except Exception:
                continue
        return "guest"

    async def _parse_authenticated_detail(self, page) -> dict:
        """Extract description + criteria + salary from authenticated SDUI/SPA layout."""
        result: dict = {
            "description": "",
            "requirements": None,
            "salary_range": None,
            "job_type": None,
            "experience_level": None,
        }

        # Click "Show more" to expand truncated descriptions. Bound the click
        # so a modal overlay can't hang us (default click timeout is 30s).
        show_more_selector = (
            "[data-sdui-component$='aboutTheJob'] [data-testid='expandable-text-button'], "
            "button.jobs-description__footer-button, "
            "button[aria-label='Show more']"
        )
        await self._maybe_click(page, show_more_selector)

        try:
            for selector in self.AUTHENTICATED_DESCRIPTION_SELECTORS:
                if await page.locator(selector).count() == 0:
                    continue
                text = (await page.locator(selector).first.text_content() or "").strip()
                text = text.removeprefix("About the job").strip()
                if text:
                    result["description"] = text
                    break

            # Fallback: h2 anchor (covers SDUI variants where the component
            # name attribute may have rotated).
            if not result["description"]:
                about_h2 = page.locator("h2:has-text('About the job')")
                if await about_h2.count() > 0:
                    container = about_h2.first.locator("../..")
                    raw = (await container.text_content() or "").strip()
                    desc = raw.removeprefix("About the job").strip()
                    if desc:
                        result["description"] = desc
        except Exception as exc:
            logger.debug("Authenticated description extraction failed: %s", exc)

        # Criteria selectors used by SPA detail pages.
        try:
            criteria = page.locator("li.jobs-unified-top-card__job-insight")
            count = await criteria.count()
            for i in range(count):
                self._classify_criterion(
                    (await criteria.nth(i).text_content() or "").strip().lower(),
                    result,
                )
        except Exception as exc:
            logger.debug("Authenticated criteria extraction failed: %s", exc)

        # Salary (SPA layout).
        try:
            salary_selector = (
                "div.salary-main-rail__data-body, span.jobs-unified-top-card__salary"
            )
            if await page.locator(salary_selector).count() > 0:
                result["salary_range"] = (
                    await page.locator(salary_selector).first.text_content() or ""
                ).strip()
        except Exception as exc:
            logger.debug("Authenticated salary extraction failed: %s", exc)

        return result

    async def _parse_guest_detail(self, page) -> dict:
        """Extract description + criteria + salary from guest JSERP layout."""
        result: dict = {
            "description": "",
            "requirements": None,
            "salary_range": None,
            "job_type": None,
            "experience_level": None,
        }

        await self._maybe_click(page, "button.show-more-less-html__button--more")

        try:
            loc = page.locator(self.GUEST_DESCRIPTION_SELECTOR)
            if await loc.count() > 0:
                text = (await loc.first.text_content() or "").strip()
                if text:
                    result["description"] = text
        except Exception as exc:
            logger.debug("Guest description extraction failed: %s", exc)

        # Guest criteria live in a different list structure.
        try:
            criteria = page.locator(
                "ul.description__job-criteria-list li, ul.job-criteria__list li"
            )
            count = await criteria.count()
            for i in range(count):
                self._classify_criterion(
                    (await criteria.nth(i).text_content() or "").strip().lower(),
                    result,
                )
        except Exception as exc:
            logger.debug("Guest criteria extraction failed: %s", exc)

        # Guest layout rarely exposes salary; left empty.
        return result

    async def _maybe_click(self, page, selector: str) -> None:
        """Best-effort click on a possibly-absent element, bounded so a modal
        overlay can't hang the scrape."""
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=2000):
                try:
                    await loc.click(timeout=3000)
                    await self.browser.random_delay(0.5, 1.0)
                except Exception as exc:
                    logger.debug("Click on %s failed: %s", selector, exc)
        except Exception:
            pass  # Element not present — continue.

    @staticmethod
    def _classify_criterion(text: str, result: dict) -> None:
        """Bucket a job-criteria string into experience_level or job_type."""
        if not text:
            return
        if any(kw in text for kw in ("entry", "associate", "mid-senior", "director", "executive")):
            result["experience_level"] = text
        elif any(
            kw in text for kw in ("full-time", "part-time", "contract", "temporary", "internship")
        ):
            result["job_type"] = text

    async def _parse_job_detail_page(self, page) -> dict:
        """Dispatch to the layout-specific detail parser.

        LinkedIn serves two distinct layouts on /jobs/view/ — authenticated
        SDUI/SPA and public guest JSERP — with mutually incompatible DOM
        structures. Detect once, then run a layout-scoped parser; this
        prevents cross-layout selector unions from leaking UI chrome (e.g.
        "Show more"/"Show less" toggle buttons) into the scraped description.
        """
        layout = await self._detect_layout(page)
        logger.debug("Detail layout detected: %s (%s)", layout, page.url)

        if layout == "authenticated":
            result = await self._parse_authenticated_detail(page)
        else:
            result = await self._parse_guest_detail(page)

        if not result["description"]:
            logger.warning(
                "Empty description on detail page: %s (layout=%s — "
                "selectors did not match or session was downgraded)",
                page.url,
                layout,
            )
        return result
