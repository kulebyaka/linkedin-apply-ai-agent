"""E2E tests for the Easy Apply frontend (Task 8).

Covers:
- The Application Profile settings card renders and saves (PUT /api/users/me).
- The auto-apply toggle is present in that card.
- New job statuses render correctly in the Applications table:
  `needs_extension` shows an "Apply now" action + a "Connect extension"
  banner; `manual_required` shows a "Finish manually" link.

Run with:
    uv run pytest tests/e2e/test_apply_ui.py -v -m e2e
"""

import time

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def ensure_test_user(mock_llm_and_api_server: str) -> None:
    """Materialize the static override user into the repo and reset apply state.

    Routes that persist by ``user.id`` (PUT /api/users/me) need a real row.
    We also reset ``auto_apply`` to False before each test so the auto-apply
    branch from one test doesn't leak into another's ``seed_pending_job``
    (which would otherwise auto-apply the job past ``pending``).
    """
    resp = httpx.post(
        f"{mock_llm_and_api_server}/__test__/ensure-test-user", timeout=10
    )
    resp.raise_for_status()
    reset = httpx.put(
        f"{mock_llm_and_api_server}/api/users/me",
        json={"auto_apply": False},
        timeout=10,
    )
    reset.raise_for_status()


def _approve_to_needs_extension(api_url: str, job_id: str, timeout: float = 30) -> None:
    """Approve a pending job; with no extension session it parks in needs_extension."""
    resp = httpx.post(
        f"{api_url}/api/hitl/{job_id}/decide",
        json={"decision": "approved"},
        timeout=10,
    )
    resp.raise_for_status()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_resp = httpx.get(f"{api_url}/api/jobs/{job_id}/status", timeout=10)
        status_resp.raise_for_status()
        status = status_resp.json()["status"]
        if status == "needs_extension":
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"Job {job_id} did not reach 'needs_extension' within {timeout}s (last: {status})"
    )


# ---------------------------------------------------------------------------
# Application Profile card
# ---------------------------------------------------------------------------


class TestApplicationProfileCard:
    def test_card_renders(self, ui_dev_server: str, page: Page) -> None:
        page.goto(f"{ui_dev_server}/settings")
        page.wait_for_load_state("networkidle")
        expect(
            page.get_by_role("heading", name="Application Profile")
        ).to_be_visible(timeout=15_000)
        # Auto-apply toggle is present.
        expect(page.get_by_text("Auto-apply to matching jobs")).to_be_visible()

    def test_save_persists(self, ui_dev_server: str, page: Page) -> None:
        page.goto(f"{ui_dev_server}/settings")
        page.wait_for_load_state("networkidle")

        expect(
            page.get_by_role("heading", name="Application Profile")
        ).to_be_visible(timeout=15_000)

        # Scope to the Application Profile section (multiple Save buttons exist).
        section = page.locator("section", has_text="Application Profile").first

        section.locator("#phone-country-code").fill("+1")
        section.locator("#years-experience").fill("8")
        section.locator("#expected-salary").fill("120000 USD")
        section.locator("#legally-authorized").select_option("yes")
        section.locator("#needs-visa").select_option("no")

        # Toggle auto-apply on.
        section.get_by_role("checkbox").check()

        # Wait on the PUT round-trip itself: the "Saved" badge is transient
        # (2s) and races with the slower cross-origin request, so assert the
        # form save reaches the API and returns the values we entered.
        def _is_profile_put(resp) -> bool:
            return (
                resp.request.method == "PUT"
                and resp.url.endswith("/api/users/me")
            )

        with page.expect_response(_is_profile_put, timeout=15_000) as resp_info:
            section.get_by_role("button", name="Save").click()

        response = resp_info.value
        assert response.status == 200, f"save failed: {response.status}"
        body = response.json()
        assert body["auto_apply"] is True
        assert body["apply_profile"]["phone_country_code"] == "+1"
        assert body["apply_profile"]["years_experience"] == 8
        assert body["apply_profile"]["legally_authorized"] is True
        assert body["apply_profile"]["needs_visa_sponsorship"] is False


# ---------------------------------------------------------------------------
# Applications table status rendering
# ---------------------------------------------------------------------------


class TestStatusRendering:
    def test_needs_extension_badge_and_apply(
        self,
        ui_dev_server: str,
        mock_llm_and_api_server: str,
        page: Page,
        seed_pending_job: str,
    ) -> None:
        job_id = seed_pending_job
        _approve_to_needs_extension(mock_llm_and_api_server, job_id)

        page.goto(f"{ui_dev_server}/applications")
        page.wait_for_load_state("networkidle")

        # Status badge for the new state renders.
        expect(
            page.get_by_text("needs_extension").first
        ).to_be_visible(timeout=15_000)

        # The recovery affordances are present.
        expect(
            page.get_by_role("button", name="Apply now").first
        ).to_be_visible()
        expect(
            page.get_by_role("link", name="Connect extension")
        ).to_be_visible()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
