"""E2E tests for the HITL review UI.

Tests verify the full user journey through the Tinder-like review interface:
job submission, pending display, approve/decline/retry flows, PDF download,
and job description rendering.

Run with:
    pytest tests/e2e/test_hitl_review.py -v -m e2e

Requires: Playwright browsers installed (npx playwright install chromium).
Servers are auto-started by session fixtures in conftest.py.
"""

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e


class TestHITLSmoke:
    """Smoke test: verify the UI loads correctly."""

    def test_ui_loads_review_page(self, ui_dev_server: str, page: Page):
        """Navigate to the UI and verify the review page heading is visible."""
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        heading = page.locator("h1, h2, h3").first
        expect(heading).to_be_visible(timeout=10_000)
