"""Capture authenticated LinkedIn cookies for the scraper.

Opens a headed Chromium window routed through an SSH SOCKS5 proxy so that
LinkedIn sees the login originating from the VPS's IP, not the local
machine. Waits for the `li_at` session cookie to appear (user has
completed login + any 2FA / captcha challenge), saves the full cookie jar
to data/linkedin_cookies.json, and exits.

The output file is the format LinkedInAutomation expects — scp it to the
VPS at /opt/linkedin-apply/data/linkedin_cookies.json and restart the API
container to pick up the fresh session.

Usage:
    # In one shell, open the SOCKS tunnel:
    ssh -i ~/.ssh/id_ed25519_vps -D 1080 -N root@37.114.41.69

    # In another, run this script:
    uv run python scripts/capture_linkedin_cookies.py

Set PROXY=socks5://127.0.0.1:1080 (or override via env) to use the tunnel.
Set PROXY= (empty) to skip the proxy entirely.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

OUTPUT_PATH = Path("data/linkedin_cookies.json").resolve()
PROXY = os.environ.get("PROXY", "socks5://127.0.0.1:1080")


async def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        launch_kwargs: dict = {"headless": False}
        if PROXY:
            launch_kwargs["proxy"] = {"server": PROXY}
            print(f">>> Routing browser traffic through: {PROXY}")
        else:
            print(">>> No proxy configured — LinkedIn will see your local IP.")

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        print("\n>>> Complete the LinkedIn login in the browser window.")
        print(">>> Includes 2FA / captcha if prompted.")
        print(">>> Waiting for `li_at` session cookie...\n")

        while True:
            cookies = await context.cookies()
            if any(c["name"] == "li_at" for c in cookies):
                break
            await asyncio.sleep(2)

        # `li_at` can be set before login completes. Verify the session is
        # actually authenticated by hitting /feed and waiting for the
        # logged-in layout — otherwise we'd ship a useless cookie jar.
        print(">>> `li_at` set. Verifying session by loading /feed...")
        while True:
            try:
                await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            except Exception as exc:
                print(f">>> /feed navigation hiccup: {exc} — retrying in 3s")
                await asyncio.sleep(3)
                continue
            current_url = page.url
            if "/feed" in current_url and "login" not in current_url and "checkpoint" not in current_url:
                print(f">>> Authenticated session confirmed (url: {current_url})")
                break
            print(f">>> Not authenticated yet (url: {current_url}). Complete any remaining steps in the browser — retrying in 5s.")
            await asyncio.sleep(5)

        # Let LinkedIn settle and issue ancillary cookies (liap, lms_*, etc.)
        await asyncio.sleep(3)
        cookies = await context.cookies()

        OUTPUT_PATH.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        OUTPUT_PATH.chmod(0o600)
        names = sorted({c["name"] for c in cookies})
        print(f">>> Saved {len(cookies)} cookies to {OUTPUT_PATH}")
        print(f">>> Includes: {', '.join(names[:15])}{'...' if len(names) > 15 else ''}")
        has_li_at = any(c["name"] == "li_at" for c in cookies)
        print(f">>> li_at present: {has_li_at}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
