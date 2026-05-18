"""Probe the AUTHENTICATED LinkedIn search SPA layout.

Mimics production: load cookies → visit /feed (validation) → then visit
/jobs/search/?... → dump selector counts + a snapshot for diagnosis.

Run inside the API container so the egress IP matches the VPS.
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

CANDIDATES = [
    "div.job-card-container",
    "div.job-search-card",
    "li.scaffold-layout__list-item",
    "li.jobs-search-results__list-item",
    "li[data-occludable-job-id]",
    "div[data-job-id]",
    "ul.jobs-search-results__list li",
    "ul.scaffold-layout__list-container li",
    "a[href*='/jobs/view/']",
    "div.jobs-search-no-results-banner",
    "div.jobs-search-results-list",
    "div.jobs-search-results__list-item",
    "li.ember-view",
]


async def main() -> int:
    async with async_playwright() as pw:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
        browser = await pw.chromium.launch(headless=True, env=clean_env)
        context = await browser.new_context(
            viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
            locale="en-US",
        )
        await Stealth().apply_stealth_async(context)
        cookies = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        page = await context.new_page()

        # Mimic _validate_session
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        print(f"After /feed: {page.url}")
        await asyncio.sleep(3)

        await page.goto(URL, wait_until="domcontentloaded")
        print(f"After search: {page.url}")
        await asyncio.sleep(5)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 700)")
            await asyncio.sleep(1.0)

        print(f"\nTitle: {await page.title()}")
        print(f"URL:   {page.url}\n")

        for sel in CANDIDATES:
            try:
                count = await page.locator(sel).count()
                print(f"  {count:4d}  {sel}")
            except Exception as exc:
                print(f"   ERR  {sel}: {exc}")

        # Dump a representative card outerHTML if anything matched
        print("\n--- Sample of first /jobs/view/ anchor's surrounding card ---")
        anchor = page.locator("a[href*='/jobs/view/']").first
        if await anchor.count() > 0:
            outer = await anchor.evaluate(
                "el => (el.closest('li[data-occludable-job-id], li.scaffold-layout__list-item, "
                "li.jobs-search-results__list-item, div.job-card-container, li.ember-view, li, div[data-job-id]')"
                " || el).outerHTML.slice(0, 2500)"
            )
            print(outer)
        else:
            print("(no /jobs/view/ anchors found)")

        snap = Path("data/linkedin_authed_snapshot.html")
        snap.write_text(await page.content(), encoding="utf-8")
        print(f"\nSnapshot: {snap}")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
