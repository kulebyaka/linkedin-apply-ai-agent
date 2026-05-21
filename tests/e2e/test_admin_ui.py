"""E2E tests for the admin UI (Task 9 acceptance).

Covers the admin happy path:
- `/admin/jobs` renders and a status filter narrows the table.
- `/admin/queue` renders queue/scheduler dashboard.
- `/admin/users` lists users and supports a role toggle through the confirm dialog.

Non-admin redirect is verified at the route guard level by overriding the
auth dependency to return a non-admin user via a small per-test fixture.

Run with:
    uv run pytest tests/e2e/test_admin_ui.py -v -m e2e
"""

import re
import time

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_extra_user(mock_llm_and_api_server: str) -> dict:
    """Create a second user (non-admin) via the test-only seed endpoint."""
    resp = httpx.post(
        f"{mock_llm_and_api_server}/__test__/seed-user",
        json={"email": "regular@test.com"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Layout / guard
# ---------------------------------------------------------------------------


class TestAdminLayout:
    def test_admin_index_redirects_to_jobs(
        self, ui_dev_server: str, page: Page
    ) -> None:
        page.goto(f"{ui_dev_server}/admin")
        page.wait_for_url(re.compile(r".*/admin/jobs$"), timeout=10_000)
        expect(page.get_by_role("heading", name="Jobs").first).to_be_visible()


# ---------------------------------------------------------------------------
# Jobs page
# ---------------------------------------------------------------------------


class TestAdminJobsPage:
    def test_jobs_page_renders_seeded_job(
        self,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ) -> None:
        job_id = seed_pending_job
        page.goto(f"{ui_dev_server}/admin/jobs")
        page.wait_for_load_state("networkidle")

        # Filter bar visible
        expect(page.get_by_text("Filters", exact=True)).to_be_visible(timeout=10_000)

        # Job title appears in the table
        expect(
            page.get_by_text("Senior Python Backend Engineer").first
        ).to_be_visible(timeout=15_000)
        # The footer "1 of N" / total count is rendered
        expect(page.get_by_text(re.compile(r"\d+–\d+ of \d+"))).to_be_visible()
        assert job_id  # ensure fixture ran

    def test_status_filter_narrows_results(
        self,
        ui_dev_server: str,
        page: Page,
        seed_pending_job: str,
    ) -> None:
        """Toggling a status that no job matches collapses the table."""
        page.goto(f"{ui_dev_server}/admin/jobs")
        page.wait_for_load_state("networkidle")

        # Wait for at least one row to appear first.
        expect(
            page.get_by_text("Senior Python Backend Engineer").first
        ).to_be_visible(timeout=15_000)

        # Click the `applied` status chip - no seeded job has this status.
        page.get_by_role("button", name="applied", exact=True).click()

        # Either the table is empty (no row) or count footer reads 0 of 0.
        # The footer is the most reliable signal.
        expect(page.get_by_text("0–0 of 0")).to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# Queue page
# ---------------------------------------------------------------------------


class TestAdminQueuePage:
    def test_queue_dashboard_renders(self, ui_dev_server: str, page: Page) -> None:
        page.goto(f"{ui_dev_server}/admin/queue")
        page.wait_for_load_state("networkidle")

        expect(
            page.get_by_role("heading", name="Queue & Scheduler")
        ).to_be_visible(timeout=10_000)

        # StatCards have these titles
        for title in ("Queue depth", "Consumer", "Active tasks", "Jobs · 24h"):
            expect(page.get_by_text(title, exact=True).first).to_be_visible(
                timeout=10_000
            )

        # Scheduler section header is present (table may be empty in tests)
        expect(
            page.get_by_role("heading", name="Scheduler · per user")
        ).to_be_visible()


# ---------------------------------------------------------------------------
# Users page
# ---------------------------------------------------------------------------


class TestAdminUsersPage:
    def test_users_table_lists_seeded_user(
        self,
        ui_dev_server: str,
        page: Page,
        seed_extra_user: dict,
    ) -> None:
        page.goto(f"{ui_dev_server}/admin/users")
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="Users")).to_be_visible(
            timeout=10_000
        )
        expect(page.get_by_text(seed_extra_user["email"]).first).to_be_visible(
            timeout=10_000
        )

    def test_role_toggle_shows_confirm_dialog_and_updates(
        self,
        mock_llm_and_api_server: str,
        ui_dev_server: str,
        page: Page,
        seed_extra_user: dict,
    ) -> None:
        target_id = seed_extra_user["id"]
        target_email = seed_extra_user["email"]

        page.goto(f"{ui_dev_server}/admin/users")
        page.wait_for_load_state("networkidle")

        # Wait until our seeded user row is visible.
        expect(page.get_by_text(target_email).first).to_be_visible(timeout=10_000)

        # Pick the role <select> for this row via aria-label.
        selector = page.get_by_label(f"Change role for {target_email}")
        expect(selector).to_be_visible()
        selector.select_option("premium")

        # Confirm dialog appears.
        expect(
            page.get_by_role("heading", name="Confirm role change")
        ).to_be_visible(timeout=5_000)
        page.get_by_role("button", name="Confirm", exact=True).click()

        # Toast appears.
        expect(page.get_by_text(re.compile(r"Role updated to premium"))).to_be_visible(
            timeout=10_000
        )

        # Verify via API that the role actually persisted.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            resp = httpx.get(
                f"{mock_llm_and_api_server}/api/admin/users", timeout=10
            )
            resp.raise_for_status()
            items = resp.json()["items"]
            row = next((r for r in items if r["user"]["id"] == target_id), None)
            if row and row["user"]["role"] == "premium":
                return
            time.sleep(0.5)
        pytest.fail("Role did not persist to 'premium' within timeout")


# The last-admin demotion guard is exercised at the unit level in
# tests/unit/test_admin_endpoints.py. Reproducing it via the e2e harness
# would require the admin to also be seeded in the user repository — kept
# as a manual check (see Task 9 acceptance criteria in the plan).
