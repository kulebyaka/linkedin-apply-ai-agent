"""Stealth browser automation for LinkedIn using Playwright.

Provides cookie-based session persistence, human-like delays/scrolling,
and anti-detection via playwright-stealth.
"""

import asyncio
import json
import logging
import os
import random
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from playwright_stealth import Stealth

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class LinkedInAutomation:
    """Automates LinkedIn browser interactions with stealth anti-detection."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.email = settings.linkedin_email
        self.password = settings.linkedin_password
        self.headless = settings.browser_headless
        self.cookie_path = Path(settings.linkedin_session_cookie_path)
        self.proxy_server = settings.linkedin_proxy_server
        self.min_delay = settings.linkedin_min_delay
        self.max_delay = settings.linkedin_max_delay
        self.page_delay_min = settings.linkedin_page_delay_min
        self.page_delay_max = settings.linkedin_page_delay_max

        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._stealth = Stealth()

    async def initialize(self) -> None:
        """Launch Playwright Chromium with stealth patches."""
        self._playwright = await async_playwright().start()

        try:
            # Randomized viewport for fingerprint diversity
            viewport_width = random.randint(1280, 1920)
            viewport_height = random.randint(800, 1080)

            # macOS fix: DYLD_LIBRARY_PATH (set for WeasyPrint to find Homebrew libs)
            # gets inherited by Chromium, forcing it to load incompatible libs and crash.
            # Strip DYLD_* vars from the child process env. No-op on Linux.
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
            launch_kwargs: dict = {"headless": self.headless, "env": clean_env}
            if self.proxy_server:
                launch_kwargs["proxy"] = {"server": self.proxy_server}
                logger.info("Routing browser through proxy: %s", self.proxy_server)
            self.browser = await self._playwright.chromium.launch(**launch_kwargs)
            self.context = await self.browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                locale="en-US",
            )

            # Apply stealth patches to context
            await self._stealth.apply_stealth_async(self.context)

            self.page = await self.context.new_page()
            logger.info("Browser initialized with stealth (viewport %dx%d)", viewport_width, viewport_height)
        except Exception:
            await self.close()
            raise

    async def _load_cookies(self) -> bool:
        """Load cookies from file and inject into browser context.

        Returns True if cookies were loaded successfully.
        """
        if not self.cookie_path.exists():
            logger.debug("No cookie file found at %s", self.cookie_path)
            return False

        try:
            cookies = json.loads(self.cookie_path.read_text(encoding="utf-8"))
            if not cookies:
                return False
            await self.context.add_cookies(cookies)
            logger.info("Loaded %d cookies from %s", len(cookies), self.cookie_path)
            return True
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load cookies: %s", exc)
            return False

    async def _save_cookies(self) -> None:
        """Save current browser context cookies to JSON file."""
        try:
            self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
            cookies = await self.context.cookies()
            self.cookie_path.write_text(
                json.dumps(cookies, indent=2), encoding="utf-8"
            )
            self.cookie_path.chmod(0o600)
            logger.info("Saved %d cookies to %s", len(cookies), self.cookie_path)
        except OSError as exc:
            logger.warning("Failed to save cookies: %s", exc)

    async def _validate_session(self) -> bool:
        """Check if current session is valid by navigating to LinkedIn feed.

        Returns True if the feed page loads (not redirected to login).
        """
        try:
            await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            current_url = self.page.url
            is_valid = "/feed" in current_url and "/login" not in current_url
            logger.info("Session validation: %s (url: %s)", "valid" if is_valid else "invalid", current_url)
            return is_valid
        except Exception as exc:
            logger.warning("Session validation failed: %s", exc)
            return False

    async def login(self) -> None:
        """Perform automated LinkedIn login with human-like typing delays."""
        logger.info("Logging in to LinkedIn as %s", self.email)
        await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Fill email with human-like typing
        email_input = self.page.locator('input#username')
        await email_input.click()
        await email_input.type(self.email, delay=random.uniform(50, 150))

        await self.random_delay(0.5, 1.5)

        # Fill password with human-like typing
        password_input = self.page.locator('input#password')
        await password_input.click()
        await password_input.type(self.password, delay=random.uniform(50, 150))

        await self.random_delay(0.5, 1.5)

        # Click sign in
        await self.page.locator('button[type="submit"]').click()

        # Wait for navigation
        await self.page.wait_for_load_state("domcontentloaded")
        await self.random_delay(2.0, 4.0)

        # Check for security challenge
        current_url = self.page.url
        if "checkpoint" in current_url or "challenge" in current_url:
            logger.warning(
                "Security challenge detected at %s. Manual intervention may be required.",
                current_url,
            )

        # Re-read URL in case challenge was resolved manually
        current_url = self.page.url

        # Verify login success
        if ("/feed" in current_url or "/mynetwork" in current_url or "/in/" in current_url) and "/login" not in current_url:
            logger.info("Login successful")
            await self._save_cookies()
        else:
            raise RuntimeError(f"LinkedIn login failed — current URL: {current_url}")

    async def ensure_authenticated(self, validate_session: bool = True) -> None:
        """Authenticate using cookies first, falling back to login.

        Args:
            validate_session: When True (default), verifies cookies by hitting
                /feed and falls back to interactive login on failure — needed
                for any flow that requires the authenticated SPA (e.g. Easy
                Apply). When False, loads cookies if present and returns
                without any LinkedIn round-trip — appropriate for scraping
                public Jobs Search pages, which serve results unauthenticated
                and where the /feed probe reliably trips LinkedIn anti-bot
                detection and burns the session.
        """
        cookie_loaded = await self._load_cookies()
        if not validate_session:
            return
        if cookie_loaded:
            session_valid = await self._validate_session()
            if session_valid:
                logger.info("Reusing existing session via cookies")
                return

        logger.info("Cookie session invalid or missing, performing login")
        await self.login()

    async def random_delay(self, min_s: float | None = None, max_s: float | None = None) -> None:
        """Sleep for a random duration between min and max seconds."""
        low = min_s if min_s is not None else self.min_delay
        high = max_s if max_s is not None else self.max_delay
        delay = random.uniform(low, high)
        await asyncio.sleep(delay)

    async def human_scroll(self, page: Page | None = None) -> None:
        """Scroll the page with random increments and pauses to simulate human behavior.

        The scroll is anti-bot ambience, not load-bearing — if LinkedIn's SPA
        navigates mid-scroll (common on the authenticated /jobs/search route,
        which soft-redirects to /jobs/collections/recommended after recognising
        the session), Playwright raises "Execution context was destroyed".
        We swallow that and bail; the caller's card-extraction step handles
        whatever DOM ends up on screen.
        """
        target = page or self.page
        num_scrolls = random.randint(2, 5)
        for _ in range(num_scrolls):
            scroll_amount = random.randint(200, 600)
            try:
                await target.evaluate(f"window.scrollBy(0, {scroll_amount})")
            except Exception as exc:
                logger.warning(
                    "human_scroll aborted (page likely navigated): %s",
                    exc,
                )
                return
            await asyncio.sleep(random.uniform(0.3, 1.0))

    async def apply_to_job(self, job_url: str, cv_path: str) -> dict:
        """Apply to a job on LinkedIn (stub for future implementation).

        Args:
            job_url: URL of the job posting
            cv_path: Path to the tailored CV PDF

        Returns:
            Dictionary with application status and details
        """
        raise NotImplementedError("Job application automation not yet implemented")

    def is_alive(self) -> bool:
        """True iff the browser, context, and page are all usable.

        After a cascade of detail-page failures Playwright can leave the
        browser handle non-None but with a closed page/context. Reusing
        such an instance breaks the very next goto with TargetClosedError,
        so callers should check this before each search cycle.
        """
        if self.browser is None or self.context is None or self.page is None:
            return False
        try:
            if not self.browser.is_connected():
                return False
        except Exception:
            return False
        try:
            if self.page.is_closed():
                return False
        except Exception:
            return False
        return True

    async def close(self) -> None:
        """Close the browser.

        Intentionally does NOT save cookies. Cookies are only persisted by
        ``login()`` after a verified successful sign-in — saving on shutdown
        would overwrite the on-disk session jar with whatever the in-memory
        state happens to be (often stale/invalidated after a long-running
        session or an anti-bot challenge), defeating the freshly-captured
        cookies pushed by the operator. Every deploy used to silently rot
        the cookie file because of this.
        """
        try:
            if self.browser:
                await self.browser.close()
        except Exception as exc:
            logger.warning("Failed to close browser: %s", exc)
        finally:
            self.browser = None
            self.page = None
            self.context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed")
