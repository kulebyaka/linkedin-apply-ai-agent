"""Probe LinkedIn search page to diagnose 0-jobs scrape.

Reuses saved cookies, navigates to the same URL the production scraper
used, and reports: page title, no-results banner presence, counts under
the production selector vs several candidate selectors, and a sample of
the first matching element's outer HTML.

Run after `linkedin_login_headed.py` has produced data/linkedin_cookies.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

COOKIE_PATH = Path("data/linkedin_cookies.json")
URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords=ai+developer&location=Prague%2C+Czechia"
    "&f_TPR=r604800&f_E=3%2C4%2C6%2C5&f_JT=F%2CP%2CC%2CT"
)

CANDIDATE_SELECTORS = [
    # Current production selector
    ("PROD job_card_container", "div.job-card-container"),
    # Common LinkedIn variants seen recently
    ("li.scaffold-layout__list-item", "li.scaffold-layout__list-item"),
    ("li.jobs-search-results__list-item", "li.jobs-search-results__list-item"),
    ("div.job-search-card", "div.job-search-card"),
    ("div[data-job-id]", "div[data-job-id]"),
    ("li[data-occludable-job-id]", "li[data-occludable-job-id]"),
    ("ul.jobs-search-results__list li", "ul.jobs-search-results__list li"),
    ("div.jobs-search-no-results-banner", "div.jobs-search-no-results-banner"),
    # Generic "looks like a job card" anchor pattern
    ("a[href*='/jobs/view/']", "a[href*='/jobs/view/']"),
]


async def main() -> int:
    if not COOKIE_PATH.exists():
        print(f"No cookie file at {COOKIE_PATH} — run linkedin_login_headed.py first.")
        return 1

    async with async_playwright() as pw:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
        # headless to match production conditions
        browser = await pw.chromium.launch(headless=True, env=clean_env)
        context = await browser.new_context(
            viewport={
                "width": random.randint(1280, 1920),
                "height": random.randint(800, 1080),
            },
            locale="en-US",
        )
        await Stealth().apply_stealth_async(context)

        cookies = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)

        page = await context.new_page()
        print(f"Navigating to: {URL}\n")
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(4)  # let SPA hydrate

        # Scroll to trigger lazy-loaded cards (mimics scraper)
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(0.8)

        title = await page.title()
        url_after = page.url
        print(f"Page title: {title}")
        print(f"Final URL:  {url_after}\n")

        for label, sel in CANDIDATE_SELECTORS:
            try:
                count = await page.locator(sel).count()
                print(f"  {count:4d}  {label}   ({sel})")
            except Exception as exc:
                print(f"   ERR  {label}   ({sel}): {exc}")

        # First sample of an anchor to /jobs/view/ (most reliable card signal)
        print("\n--- Sample DOM around first /jobs/view/ anchor ---")
        anchor = page.locator("a[href*='/jobs/view/']").first
        if await anchor.count() > 0:
            outer = await anchor.evaluate(
                "el => el.closest('li, div[class*=\"job-card\"], div[data-job-id]')?.outerHTML?.slice(0, 1200) || el.outerHTML.slice(0, 1200)"
            )
            print(outer)
        else:
            print("(no anchors found)")

        # Save page HTML snapshot for offline inspection
        snapshot = Path("data/linkedin_search_snapshot.html")
        snapshot.write_text(await page.content(), encoding="utf-8")
        print(f"\nFull HTML snapshot saved to {snapshot}")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
