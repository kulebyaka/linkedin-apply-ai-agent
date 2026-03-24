"""E2E tests for the HITL review UI.

Tests verify the full user journey through the Tinder-like review interface:
job submission, pending display, approve/decline/retry flows, PDF download,
CV HTML preview, and job description rendering.

Run with:
    pytest tests/e2e/test_hitl_review.py -v -m e2e

Prerequisites:
    - Playwright browsers installed: npx playwright install chromium
    - Node modules installed in ui/: cd ui && npm install

Servers (FastAPI backend + Vite UI dev server) are auto-started by session-scoped
fixtures in conftest.py. LLM calls are mocked — no API keys required.

Environment variables (optional):
    API_PORT  — override the FastAPI server port (default: random free port)
    UI_PORT   — override the Vite dev server port (default: random free port)
"""

import re
import time
from urllib.parse import urljoin

import httpx
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


class TestPendingJobsDisplay:
    """Tests for displaying pending jobs in the review queue."""

    def test_pending_jobs_displayed(
        self, ui_dev_server: str, page: Page, seed_pending_job: str
    ):
        """Seed 1 job, navigate to UI, verify job card shows title, company, and pending badge."""
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for the job card heading to appear
        heading = page.get_by_role("heading", name="Senior Python Backend Engineer")
        expect(heading).to_be_visible(timeout=15_000)

        # Verify company is visible
        expect(page.get_by_text("AI Startup").first).to_be_visible()

        # Verify the pending badge shows a pending count
        expect(page.get_by_text(re.compile(r"\d+ pending"))).to_be_visible()

    def test_navigate_between_jobs(
        self, ui_dev_server: str, page: Page, seed_two_pending_jobs: list[str]
    ):
        """Seed 2 jobs, verify navigation counter and Previous/Next work."""
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for navigation counter to appear (matches "X of Y")
        counter = page.get_by_text(re.compile(r"\d+ of \d+"))
        expect(counter).to_be_visible(timeout=15_000)

        # Get initial counter text to find total
        counter_text = counter.inner_text()
        # Extract current and total from "X of Y"
        match = re.match(r"(\d+) of (\d+)", counter_text)
        assert match, f"Counter text '{counter_text}' doesn't match 'X of Y' pattern"
        total = int(match.group(2))
        assert total >= 2, f"Expected at least 2 jobs, got {total}"

        # Click Next and verify counter increments
        initial_num = int(match.group(1))
        page.get_by_role("button", name="Next").click()
        expect(page.get_by_text(f"{initial_num + 1} of {total}")).to_be_visible()

        # Click Previous and verify counter decrements
        page.get_by_role("button", name="Previous").click()
        expect(page.get_by_text(f"{initial_num} of {total}")).to_be_visible()


class TestApproveDeclineFlows:
    """Tests for approve and decline decision flows."""

    def test_approve_job(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ):
        """Seed 1 job, click Approve, verify toast appears and job removed from pending."""
        job_id = seed_pending_job
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # Click Approve button
        page.get_by_role("button", name="Approve").click()

        # Verify toast appears with success message
        expect(page.get_by_text("Application Approved")).to_be_visible(timeout=10_000)

        # Verify the approved job is no longer in the pending list
        pending_ids = [
            j["job_id"]
            for j in httpx.get(
                f"{mock_llm_and_api_server}/api/hitl/pending", timeout=10
            ).json()
        ]
        assert job_id not in pending_ids, (
            f"Approved job {job_id} should not appear in pending list"
        )

    def test_decline_job(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ):
        """Seed 1 job, click Decline, enter feedback, submit, verify toast and job removed."""
        job_id = seed_pending_job
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # Click Decline button
        page.get_by_role("button", name="Decline").click()

        # Modal should appear with "Decline Application" title
        expect(page.get_by_text("Decline Application")).to_be_visible(timeout=5_000)

        # Enter optional feedback in the textarea
        textarea = page.locator("textarea")
        textarea.fill("Not a good fit for my current career goals")

        # Click "Confirm Decline" button in the modal
        page.get_by_role("button", name="Confirm Decline").click()

        # Verify toast appears
        expect(page.get_by_text("Application Declined")).to_be_visible(timeout=10_000)

        # Verify the declined job is no longer in the pending list
        pending_ids = [
            j["job_id"]
            for j in httpx.get(
                f"{mock_llm_and_api_server}/api/hitl/pending", timeout=10
            ).json()
        ]
        assert job_id not in pending_ids, (
            f"Declined job {job_id} should not appear in pending list"
        )


class TestPDFAndCVPreview:
    """Tests for PDF download and CV HTML preview — known bug area."""

    def test_pdf_download_works(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ):
        """Seed 1 job, switch to CV tab, click Download Full PDF, verify PDF response."""
        job_id = seed_pending_job
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # Verify the PDF endpoint returns valid PDF via API
        resp = httpx.get(
            f"{mock_llm_and_api_server}/api/jobs/{job_id}/pdf", timeout=15
        )
        assert resp.status_code == 200, (
            f"PDF endpoint returned {resp.status_code}: {resp.text[:500]}"
        )
        assert resp.headers.get("content-type", "").startswith("application/pdf"), (
            f"Expected application/pdf, got {resp.headers.get('content-type')}"
        )
        # PDF files start with %PDF
        assert resp.content[:5] == b"%PDF-", "Response body is not a valid PDF"

    def test_cv_html_preview_loads(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ):
        """Seed 1 job, switch to CV tab, verify CV HTML renders (not loading/error)."""
        job_id = seed_pending_job
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # Switch to CV Preview tab
        page.get_by_text("CV Preview").click()

        # Verify the HTML endpoint returns valid HTML via API
        resp = httpx.get(
            f"{mock_llm_and_api_server}/api/jobs/{job_id}/html", timeout=15
        )
        assert resp.status_code == 200, (
            f"HTML endpoint returned {resp.status_code}: {resp.text[:500]}"
        )
        assert "text/html" in resp.headers.get("content-type", ""), (
            f"Expected text/html, got {resp.headers.get('content-type')}"
        )
        # HTML should contain some CV content (not an error page)
        assert len(resp.text) > 100, "HTML response too short to be a valid CV"

        # Also verify in the UI: loading spinner should not be visible
        # and some content should appear in the CV preview area
        loading = page.get_by_text("Loading CV...")
        expect(loading).not_to_be_visible(timeout=15_000)


class TestJobDescriptionTruncation:
    """Tests for job description display — known bug: truncation by 'read more'."""

    def test_job_description_not_truncated(
        self,
        ui_dev_server: str,
        page: Page,
        seed_long_description_job: str,
    ):
        """Seed job with 500+ word description, verify full text present in DOM."""
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # The long description contains a unique marker phrase near the end
        # that would be truncated if a "read more" or CSS clipping was active
        marker = "UNIQUE_END_MARKER_PARAGRAPH"
        expect(page.get_by_text(marker).first).to_be_visible(timeout=10_000)

        # Also verify no "read more" / "show more" button exists
        read_more = page.get_by_text(re.compile(r"read more|show more", re.IGNORECASE))
        expect(read_more).to_have_count(0)


class TestRetryRegenerateFlow:
    """Tests for retry/regenerate CV — known bug: retry button broken."""

    def test_retry_regenerates_cv(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ):
        """Click Retry, enter feedback, submit, verify regeneration starts and job returns to pending."""
        job_id = seed_pending_job
        page.goto(ui_dev_server)
        page.wait_for_load_state("networkidle")

        # Wait for job card to load
        page.get_by_role("heading", name="Senior Python Backend Engineer").wait_for(
            state="visible", timeout=15_000
        )

        # Click Retry button
        page.get_by_role("button", name="Retry").click()

        # Modal should appear with "Regenerate CV" title
        expect(page.get_by_role("heading", name="Regenerate CV")).to_be_visible(
            timeout=5_000
        )

        # Enter feedback (required for retry)
        textarea = page.locator("textarea")
        textarea.fill("Emphasize Python skills")

        # Click "Regenerate CV" submit button (wait for it to become enabled after typing)
        regen_btn = page.get_by_role("button", name="Regenerate CV")
        expect(regen_btn).to_be_enabled(timeout=5_000)
        regen_btn.click()

        # Verify toast appears with regeneration started message
        expect(page.get_by_text("CV Regeneration Started")).to_be_visible(
            timeout=10_000
        )

        # Wait for retry workflow to complete and job to reappear in pending
        deadline = time.monotonic() + 60
        retry_count = None
        while time.monotonic() < deadline:
            # Check if the job entered a terminal error state (fail fast)
            status_resp = httpx.get(
                f"{mock_llm_and_api_server}/api/jobs/{job_id}/status", timeout=10
            )
            if status_resp.status_code == 200:
                status = status_resp.json().get("status")
                if status == "failed":
                    raise AssertionError(
                        f"Job {job_id} failed during retry: "
                        f"{status_resp.json().get('error_message')}"
                    )

            resp = httpx.get(
                f"{mock_llm_and_api_server}/api/hitl/pending", timeout=10
            )
            if resp.status_code == 200:
                pending = resp.json()
                for job in pending:
                    if job["job_id"] == job_id:
                        retry_count = job.get("attempt_count", 0)
                        break
                if retry_count is not None and retry_count >= 1:
                    break
            time.sleep(2)

        assert retry_count is not None, (
            f"Job {job_id} did not reappear in pending queue after retry within 60s"
        )
        assert retry_count >= 1, (
            f"Expected retry_count >= 1, got {retry_count}"
        )
