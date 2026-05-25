"""Quick test: load the saved detail.html and verify our extraction logic
against it using Playwright (so we get identical CSS semantics to production).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.async_api import async_playwright

HTML_PATH = Path("data/linkedin_debug/detail.html").resolve()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{HTML_PATH}", wait_until="domcontentloaded")

        # Test 1: current h2-based path
        about_h2 = page.locator("h2:has-text('About the job')")
        h2_count = await about_h2.count()
        print(f"h2 count: {h2_count}")
        if h2_count > 0:
            container = about_h2.first.locator("../..")
            raw = (await container.text_content() or "").strip()
            print(f"  h2 ../.. text_len: {len(raw)}")
            print(f"  preview: {raw[:200]!r}")

        # Test 2: new SDUI scoped selector (ends-with attribute match)
        from src.services.linkedin.linkedin_scraper import SELECTORS as S
        sdui = page.locator(S["detail_description"])
        sdui_count = await sdui.count()
        print(f"\nSDUI scoped selector count: {sdui_count} (selector: {S['detail_description']})")
        if sdui_count > 0:
            text = (await sdui.first.text_content() or "").strip()
            print(f"  text_len: {len(text)}")
            print(f"  preview: {text[:200]!r}")

        # Test 3: ends-with attribute selector for robustness
        sdui2 = page.locator("[data-sdui-component$='aboutTheJob'] [data-testid='expandable-text-box']")
        c2 = await sdui2.count()
        print(f"\nSDUI $= selector count: {c2}")

        # Test 4: just by testid (returns 2)
        all_boxes = page.locator("[data-testid='expandable-text-box']")
        print(f"\nall expandable-text-box count: {await all_boxes.count()}")

        await browser.close()


asyncio.run(main())
