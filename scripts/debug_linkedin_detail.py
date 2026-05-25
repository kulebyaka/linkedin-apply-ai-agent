"""Debug script: capture a LinkedIn job detail page from an AUTHENTICATED session.

Launches a headed browser, loads any saved cookies, and waits for the user to
finish login (if needed) before navigating to the job search page, picking a
job, and dumping the authenticated detail-page HTML for selector inspection.

Usage:
    uv run python scripts/debug_linkedin_detail.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Settings
from src.services.linkedin.browser_automation import LinkedInAutomation
from src.services.linkedin.linkedin_scraper import SELECTORS, LinkedInJobScraper
from src.services.linkedin.linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder

CANDIDATE_DESCRIPTION_SELECTORS = [
    # Authenticated SPA
    "div.jobs-description__content",
    "div#job-details",
    "article.jobs-description__container",
    "div.jobs-box__html-content",
    "div.jobs-description-content__text",
    "div.jobs-description",
    "div.mt4",
    # Public guest JSERP
    "div.show-more-less-html__markup",
    "div.description__text",
    "section.show-more-less-html",
    "section.description",
    "section.core-section-container",
]


async def wait_for_authentication(browser: LinkedInAutomation) -> None:
    """Open LinkedIn and pause until the user is logged in.

    Detected by presence of the `li_at` cookie. The user can complete login
    (including 2FA / captcha) manually in the headed browser window.
    """
    await browser.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    print("\n>>> Please complete LinkedIn login in the opened browser window.")
    print(">>> Waiting for `li_at` cookie to appear (auth complete)...")

    while True:
        cookies = await browser.context.cookies()
        if any(c["name"] == "li_at" for c in cookies):
            print(">>> Auth detected (li_at cookie present).")
            return
        await asyncio.sleep(2)


async def main() -> None:
    settings = Settings()
    # Force headed mode regardless of env so the user can log in.
    settings.browser_headless = False
    browser = LinkedInAutomation(settings)
    out_dir = Path("data/linkedin_debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    await browser.initialize()
    try:
        # Try cookies first; if no li_at, prompt for interactive login.
        await browser._load_cookies()
        cookies = await browser.context.cookies()
        has_li_at = any(c["name"] == "li_at" for c in cookies)

        if not has_li_at:
            print("[auth] No li_at cookie — interactive login required.")
            await wait_for_authentication(browser)
            await browser._save_cookies()
        else:
            print("[auth] Found li_at cookie, assuming session valid.")

        scraper = LinkedInJobScraper(browser, settings)
        params = LinkedInSearchParams(
            keywords=settings.linkedin_search_keywords or "python developer",
            location=settings.linkedin_search_location or "United States",
            max_jobs=3,
            easy_apply_only=False,
            date_posted=settings.linkedin_search_date_posted,
        )

        # Step 1: load search page (authenticated SPA layout expected)
        search_url = LinkedInSearchURLBuilder.build_url(params, page=0)
        print(f"\n[search] {search_url}")
        await browser.page.goto(search_url, wait_until="domcontentloaded")
        await browser.random_delay(3.0, 5.0)
        await browser.human_scroll()
        await browser.random_delay(2.0, 3.0)

        print(f"[search] final URL: {browser.page.url}")
        search_html = await browser.page.content()
        (out_dir / "search.html").write_text(search_html, encoding="utf-8")
        print(f"[search] saved {len(search_html)} chars to data/linkedin_debug/search.html")

        cards = browser.page.locator(SELECTORS["job_card"])
        n = await cards.count()
        print(f"[search] {n} cards found with selector: {SELECTORS['job_card']}")
        if n == 0:
            print("[search] No cards found, aborting")
            return

        job = await scraper._parse_job_card(cards.first)
        if job is None:
            print("[search] First card parse returned None")
            return
        print(f"[search] First job: id={job.job_id} title={job.title!r} url={job.url}")

        # Step 2: navigate to detail page (authenticated route)
        print(f"\n[detail] navigating to {job.url}")
        await browser.page.goto(job.url, wait_until="domcontentloaded", timeout=20000)
        await browser.random_delay(3.0, 5.0)
        await browser.human_scroll()
        await browser.random_delay(1.5, 2.5)

        final_url = browser.page.url
        print(f"[detail] final URL: {final_url}")

        # Try to expand "Show more"
        try:
            show_more = browser.page.locator(SELECTORS["detail_show_more"]).first
            if await show_more.is_visible(timeout=2000):
                try:
                    await show_more.click(timeout=3000)
                    await browser.random_delay(0.5, 1.0)
                    print("[detail] clicked show more")
                except Exception as exc:
                    print(f"[detail] show more click failed: {exc}")
        except Exception as exc:
            print(f"[detail] show more not present: {exc}")

        detail_html = await browser.page.content()
        (out_dir / "detail.html").write_text(detail_html, encoding="utf-8")
        print(f"[detail] saved {len(detail_html)} chars to data/linkedin_debug/detail.html")

        # Step 3: try every candidate selector
        print("\n[selectors] testing description candidates:")
        for sel in CANDIDATE_DESCRIPTION_SELECTORS:
            try:
                loc = browser.page.locator(sel)
                count = await loc.count()
                if count > 0:
                    text = (await loc.first.text_content() or "").strip()
                    print(f"  ✓ {sel!r:60s} count={count} text_len={len(text)}")
                    if text:
                        print(f"      preview: {text[:120]!r}")
                else:
                    print(f"  ✗ {sel!r:60s} count=0")
            except Exception as exc:
                print(f"  ! {sel!r:60s} error: {exc}")

        # Step 4: test current SELECTORS
        print("\n[selectors] current SELECTORS['detail_description']:")
        cur = SELECTORS["detail_description"]
        loc = browser.page.locator(cur)
        count = await loc.count()
        text = (await loc.first.text_content() or "").strip() if count > 0 else ""
        print(f"  count={count} text_len={len(text)}")
        if text:
            print(f"  preview: {text[:200]!r}")

        # Step 5: h2 fallback
        about_h2 = browser.page.locator("h2:has-text('About the job')")
        print(f"\n[selectors] h2:has-text('About the job') count={await about_h2.count()}")

        # Step 6: check whether this is SPA or guest layout
        is_authenticated_layout = "/feed" in (
            await browser.page.evaluate("document.body.className") or ""
        ) or "global-nav" in (
            await browser.page.evaluate("document.body.innerHTML") or ""
        )[:50000]
        print(f"\n[layout] looks authenticated: {is_authenticated_layout}")
        print(f"[layout] final URL: {final_url}")

    finally:
        print("\nPress Enter to close the browser...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except Exception:
            pass
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
