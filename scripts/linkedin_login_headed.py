"""Headed LinkedIn login helper.

Opens a real Chromium window with the same stealth patches we use in
production, navigates to the LinkedIn login page, and waits for you to
sign in by hand (solving any checkpoint/CAPTCHA interactively).

When the URL settles on `/feed`, cookies are persisted to
`data/linkedin_cookies.json` (same path the API container reads via the
bind mount). `scp` that file to the VPS afterwards.

Usage:
    # Plain — login from your local IP.
    uv run python scripts/linkedin_login_headed.py

    # Login while exiting via the VPS — set up an SSH SOCKS5 tunnel
    # in another terminal first:
    #   ssh -D 1080 -N -i ~/.ssh/id_ed25519_vps root@37.114.41.69
    # Then route Chromium through it so LinkedIn sees the VPS IP:
    VPS_PROXY=socks5://127.0.0.1:1080 \
        uv run python scripts/linkedin_login_headed.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

COOKIE_PATH = Path("data/linkedin_cookies.json")
LOGIN_URL = "https://www.linkedin.com/login"
SUCCESS_MARKERS = ("/feed", "/mynetwork", "/in/")
POLL_INTERVAL_S = 2.0
MAX_WAIT_S = 600  # 10 minutes for manual login + challenge


async def main() -> int:
    proxy = os.environ.get("VPS_PROXY")
    print(f"Cookies will be saved to: {COOKIE_PATH.resolve()}")
    if proxy:
        print(f"Routing browser traffic through proxy: {proxy}")
    print("Log in manually in the window that opens (solve any challenge).")
    print(f"Waiting up to {MAX_WAIT_S // 60} minutes for you to reach the feed...\n")

    async with async_playwright() as pw:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
        launch_kwargs: dict = {"headless": False, "env": clean_env}
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={
                "width": random.randint(1280, 1920),
                "height": random.randint(800, 1080),
            },
            locale="en-US",
        )
        await Stealth().apply_stealth_async(context)
        page = await context.new_page()

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        waited = 0.0
        while waited < MAX_WAIT_S:
            url = page.url
            if any(m in url for m in SUCCESS_MARKERS) and "/login" not in url:
                print(f"Detected logged-in URL: {url}")
                break
            await asyncio.sleep(POLL_INTERVAL_S)
            waited += POLL_INTERVAL_S
        else:
            print(f"Timed out after {MAX_WAIT_S}s without reaching the feed.")
            print(f"Current URL: {page.url}")
            await browser.close()
            return 1

        async def snapshot_cookies(reason: str) -> None:
            try:
                cookies = await context.cookies()
            except Exception as exc:
                print(f"[{reason}] cookie snapshot failed: {exc}")
                return
            COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_PATH.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            COOKIE_PATH.chmod(0o600)
            print(f"[{reason}] Saved {len(cookies)} cookies to {COOKIE_PATH}")

        await snapshot_cookies("initial")

        if os.environ.get("KEEP_OPEN", "1") != "0":
            print(
                "\nBrowser will stay open until you close the window.\n"
                "Cookies are re-snapshotted every 10s while it's open.\n"
            )
            disconnected = asyncio.Event()
            browser.on("disconnected", lambda _b: disconnected.set())

            async def periodic_snapshot() -> None:
                while not disconnected.is_set():
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        await snapshot_cookies("periodic")

            snapshot_task = asyncio.create_task(periodic_snapshot())
            await disconnected.wait()
            snapshot_task.cancel()
            print("Browser window closed by user.")

        # If browser is still connected (KEEP_OPEN disabled), close cleanly.
        if browser.is_connected():
            await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
