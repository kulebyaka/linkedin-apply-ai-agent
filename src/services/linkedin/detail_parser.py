"""LinkedIn job detail page parser.

LinkedIn serves two distinct layouts on /jobs/view/:

- authenticated SDUI/SPA
- public guest JSERP

The two have mutually incompatible DOM structures, so we detect the layout
once and dispatch to a layout-scoped parser. Mixing selectors across layouts
risks leaking UI chrome ("Show more"/"Show less" buttons) into descriptions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .selectors import (
    AUTHENTICATED_DESCRIPTION_SELECTORS,
    AUTHENTICATED_LAYOUT_MARKERS,
    GUEST_DESCRIPTION_SELECTOR,
)

if TYPE_CHECKING:
    from .browser_automation import LinkedInAutomation

logger = logging.getLogger(__name__)


class DetailPageParser:
    """Parses LinkedIn /jobs/view/<id> pages across authenticated + guest layouts."""

    def __init__(self, browser: "LinkedInAutomation"):
        self.browser = browser

    async def parse(self, page) -> dict:
        """Dispatch to the layout-specific detail parser.

        Returns the extracted fields dict (description, requirements,
        salary_range, job_type, experience_level).
        """
        layout = await self._detect_layout(page)
        logger.debug("Detail layout detected: %s (%s)", layout, page.url)

        if layout == "authenticated":
            result = await self._parse_authenticated(page)
        else:
            result = await self._parse_guest(page)

        if not result["description"]:
            logger.warning(
                "Empty description on detail page: %s (layout=%s — "
                "selectors did not match or session was downgraded)",
                page.url,
                layout,
            )
        return result

    async def _detect_layout(self, page) -> str:
        """Return 'authenticated' if SDUI/SPA markers are present, else 'guest'."""
        for marker in AUTHENTICATED_LAYOUT_MARKERS:
            try:
                if await page.locator(marker).count() > 0:
                    return "authenticated"
            except Exception:
                continue
        return "guest"

    async def _parse_authenticated(self, page) -> dict:
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
            for selector in AUTHENTICATED_DESCRIPTION_SELECTORS:
                if await page.locator(selector).count() == 0:
                    continue
                # inner_text() returns the *rendered* text, preserving the
                # newlines between block elements (<p>, <li>, headings). Using
                # text_content() here collapses the whole section into one line
                # and glues words across block boundaries ("...teamWe are...").
                text = (await page.locator(selector).first.inner_text() or "").strip()
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
                    raw = (await container.inner_text() or "").strip()
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

    async def _parse_guest(self, page) -> dict:
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
            loc = page.locator(GUEST_DESCRIPTION_SELECTOR)
            if await loc.count() > 0:
                # inner_text() preserves block-level newlines; text_content()
                # would collapse the markup into one unformatted line.
                text = (await loc.first.inner_text() or "").strip()
                if text:
                    result["description"] = text
        except Exception as exc:
            logger.debug("Guest description extraction failed: %s", exc)

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
