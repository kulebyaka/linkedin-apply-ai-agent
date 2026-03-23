"""Tests for LinkedInJobScraper with mocked Playwright page elements."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from src.models.job import ScrapedJob
from src.services.linkedin_scraper import (
    LinkedInJobScraper,
    _extract_job_id_from_url,
    _parse_relative_time,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _make_mock_settings():
    s = MagicMock()
    s.linkedin_min_delay = 0.0
    s.linkedin_max_delay = 0.0
    s.linkedin_page_delay_min = 0.0
    s.linkedin_page_delay_max = 0.0
    s.linkedin_search_max_jobs = 50
    return s


def _make_mock_browser(page=None):
    browser = MagicMock()
    browser.page = page or AsyncMock()
    browser.random_delay = AsyncMock()
    browser.human_scroll = AsyncMock()
    browser.page_delay_min = 0.0
    browser.page_delay_max = 0.0
    return browser


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestExtractJobIdFromUrl:
    def test_view_url(self):
        assert _extract_job_id_from_url("/jobs/view/1234567890/") == "1234567890"

    def test_current_job_id_param(self):
        assert _extract_job_id_from_url("?currentJobId=9876543210") == "9876543210"

    def test_ambiguous_url_returns_none(self):
        assert _extract_job_id_from_url("/jobs/collections/12345678") is None

    def test_no_match(self):
        assert _extract_job_id_from_url("/about") is None

    def test_full_url(self):
        url = "https://www.linkedin.com/jobs/view/3987654321/?trackingId=abc"
        assert _extract_job_id_from_url(url) == "3987654321"


class TestParseRelativeTime:
    def test_days_ago(self):
        result = _parse_relative_time("2 days ago")
        assert result is not None
        # Should be approximately 2 days ago
        expected = datetime.now(tz=timezone.utc) - timedelta(days=2)
        assert abs((result - expected).total_seconds()) < 5

    def test_hours_ago(self):
        result = _parse_relative_time("5 hours ago")
        assert result is not None
        expected = datetime.now(tz=timezone.utc) - timedelta(hours=5)
        assert abs((result - expected).total_seconds()) < 5

    def test_week_ago(self):
        result = _parse_relative_time("1 week ago")
        assert result is not None
        expected = datetime.now(tz=timezone.utc) - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 5

    def test_invalid_string(self):
        assert _parse_relative_time("posted recently") is None

    def test_empty_string(self):
        assert _parse_relative_time("") is None


# ---------------------------------------------------------------------------
# _parse_job_card tests
# ---------------------------------------------------------------------------


class TestParseJobCard:
    @pytest.fixture
    def scraper(self):
        return LinkedInJobScraper(_make_mock_browser(), _make_mock_settings())

    async def test_parses_complete_card(self, scraper):
        """Test parsing a job card with all fields present."""
        card = MagicMock()
        card.get_attribute = AsyncMock(return_value="1234567890")

        # Title locator
        title_loc = MagicMock()
        title_loc.first = MagicMock()
        title_loc.first.get_attribute = AsyncMock(
            side_effect=lambda attr: (
                "Senior Python Developer"
                if attr == "aria-label"
                else "/jobs/view/1234567890/?refId=abc"
            )
        )
        title_loc.first.text_content = AsyncMock(return_value="  Senior Python Developer  ")

        # Company locator
        company_loc = MagicMock()
        company_loc.count = AsyncMock(return_value=1)
        company_loc.first = MagicMock()
        company_loc.first.text_content = AsyncMock(return_value="  Acme Corp  ")

        # Location locator
        location_loc = MagicMock()
        location_loc.count = AsyncMock(return_value=1)
        location_loc.first = MagicMock()
        location_loc.first.text_content = AsyncMock(return_value="  Remote  ")

        # Easy apply locator
        easy_apply_loc = MagicMock()
        easy_apply_loc.count = AsyncMock(return_value=1)

        # Posted date locator
        posted_loc = MagicMock()
        posted_loc.count = AsyncMock(return_value=1)
        posted_loc.first = MagicMock()
        posted_loc.first.text_content = AsyncMock(return_value="3 days ago")

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            mapping = {
                SELECTORS["job_card_title"]: title_loc,
                SELECTORS["job_card_company"]: company_loc,
                SELECTORS["job_card_location"]: location_loc,
                SELECTORS["job_card_easy_apply"]: easy_apply_loc,
                SELECTORS["job_card_posted"]: posted_loc,
            }
            return mapping.get(
                selector,
                MagicMock(
                    first=MagicMock(
                        text_content=AsyncMock(return_value=""),
                        get_attribute=AsyncMock(return_value=""),
                    ),
                    count=AsyncMock(return_value=0),
                ),
            )

        card.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_card(card)

        assert isinstance(result, ScrapedJob)
        assert result.job_id == "1234567890"
        assert result.title == "Senior Python Developer"
        assert result.company == "Acme Corp"
        assert result.location == "Remote"
        assert result.easy_apply is True
        assert result.url == "https://www.linkedin.com/jobs/view/1234567890/"
        assert result.posted_date is not None

    async def test_returns_none_on_error(self, scraper):
        """Card that raises an exception returns None."""
        card = MagicMock()
        card.locator = MagicMock(side_effect=Exception("Element not found"))

        result = await scraper._parse_job_card(card)
        assert result is None

    async def test_no_easy_apply_badge(self, scraper):
        """Card without Easy Apply badge sets easy_apply=False."""
        card = MagicMock()
        card.get_attribute = AsyncMock(return_value="99999999")

        title_loc = MagicMock()
        title_loc.first = MagicMock()
        title_loc.first.get_attribute = AsyncMock(
            side_effect=lambda attr: "Developer" if attr == "aria-label" else "/jobs/view/99999999/"
        )
        title_loc.first.text_content = AsyncMock(return_value="Developer")

        default_loc = MagicMock()
        default_loc.count = AsyncMock(return_value=0)
        default_loc.first = MagicMock()
        default_loc.first.text_content = AsyncMock(return_value="")

        easy_apply_loc = MagicMock()
        easy_apply_loc.count = AsyncMock(return_value=0)

        posted_loc = MagicMock()
        posted_loc.count = AsyncMock(return_value=0)
        posted_loc.first = MagicMock()
        posted_loc.first.text_content = AsyncMock(return_value="")

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["job_card_title"]:
                return title_loc
            if selector == SELECTORS["job_card_easy_apply"]:
                return easy_apply_loc
            if selector == SELECTORS["job_card_posted"]:
                return posted_loc
            return default_loc

        card.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_card(card)
        assert isinstance(result, ScrapedJob)
        assert result.easy_apply is False

    async def test_strips_verification_badge_from_title(self, scraper):
        """'with verification' suffix from LinkedIn badge is stripped from title."""
        card = MagicMock()
        card.get_attribute = AsyncMock(return_value="1111111111")

        title_loc = MagicMock()
        title_loc.first = MagicMock()
        title_loc.first.get_attribute = AsyncMock(
            side_effect=lambda attr: (
                "AI Engineer, Entry Level with verification"
                if attr == "aria-label"
                else "/jobs/view/1111111111/"
            )
        )
        title_loc.first.text_content = AsyncMock(return_value="AI Engineer, Entry Level")

        default_loc = MagicMock()
        default_loc.count = AsyncMock(return_value=0)
        default_loc.first = MagicMock()
        default_loc.first.text_content = AsyncMock(return_value="")

        easy_apply_loc = MagicMock()
        easy_apply_loc.count = AsyncMock(return_value=0)

        posted_loc = MagicMock()
        posted_loc.count = AsyncMock(return_value=0)
        posted_loc.first = MagicMock()
        posted_loc.first.text_content = AsyncMock(return_value="")

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["job_card_title"]:
                return title_loc
            if selector == SELECTORS["job_card_easy_apply"]:
                return easy_apply_loc
            if selector == SELECTORS["job_card_posted"]:
                return posted_loc
            return default_loc

        card.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_card(card)
        assert isinstance(result, ScrapedJob)
        assert result.title == "AI Engineer, Entry Level"


# ---------------------------------------------------------------------------
# _parse_job_detail_page tests
# ---------------------------------------------------------------------------


def _make_show_more_loc(visible=False):
    """Create a mock for the 'Show more' button locator."""
    loc = MagicMock()
    loc.first = MagicMock()
    loc.first.is_visible = AsyncMock(return_value=visible)
    loc.first.click = AsyncMock()
    return loc


class TestParseJobDetailPage:
    @pytest.fixture
    def scraper(self):
        return LinkedInJobScraper(_make_mock_browser(), _make_mock_settings())

    async def test_parses_description(self, scraper):
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=False)

        desc_loc = MagicMock()
        desc_loc.count = AsyncMock(return_value=1)
        desc_loc.first = MagicMock()
        desc_loc.first.text_content = AsyncMock(
            return_value="  We are looking for a Python developer.  "
        )

        criteria_loc = MagicMock()
        criteria_loc.count = AsyncMock(return_value=0)

        salary_loc = MagicMock()
        salary_loc.count = AsyncMock(return_value=0)

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            if selector == SELECTORS["detail_description"]:
                return desc_loc
            if selector == SELECTORS["detail_criteria"]:
                return criteria_loc
            if selector == SELECTORS["detail_salary"]:
                return salary_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        assert result["description"] == "We are looking for a Python developer."

    async def test_clicks_show_more_when_visible(self, scraper):
        """Verify the scraper clicks 'Show more' before extracting description."""
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=True)

        desc_loc = MagicMock()
        desc_loc.count = AsyncMock(return_value=1)
        desc_loc.first = MagicMock()
        desc_loc.first.text_content = AsyncMock(return_value="Full expanded description text.")

        criteria_loc = MagicMock()
        criteria_loc.count = AsyncMock(return_value=0)

        salary_loc = MagicMock()
        salary_loc.count = AsyncMock(return_value=0)

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            if selector == SELECTORS["detail_description"]:
                return desc_loc
            if selector == SELECTORS["detail_criteria"]:
                return criteria_loc
            if selector == SELECTORS["detail_salary"]:
                return salary_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        show_more_loc.first.click.assert_awaited_once()
        assert result["description"] == "Full expanded description text."

    async def test_parses_criteria(self, scraper):
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=False)

        desc_loc = MagicMock()
        desc_loc.count = AsyncMock(return_value=0)
        desc_loc.first = MagicMock()
        desc_loc.first.text_content = AsyncMock(return_value="")

        # Two criteria items
        criteria_item_1 = MagicMock()
        criteria_item_1.text_content = AsyncMock(return_value="Mid-Senior level")
        criteria_item_2 = MagicMock()
        criteria_item_2.text_content = AsyncMock(return_value="Full-time")

        criteria_loc = MagicMock()
        criteria_loc.count = AsyncMock(return_value=2)
        criteria_loc.nth = MagicMock(side_effect=lambda i: [criteria_item_1, criteria_item_2][i])

        salary_loc = MagicMock()
        salary_loc.count = AsyncMock(return_value=0)

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            if selector == SELECTORS["detail_description"]:
                return desc_loc
            if selector == SELECTORS["detail_criteria"]:
                return criteria_loc
            if selector == SELECTORS["detail_salary"]:
                return salary_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        assert "mid-senior" in result["experience_level"]
        assert "full-time" in result["job_type"]

    async def test_parses_salary(self, scraper):
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=False)

        desc_loc = MagicMock()
        desc_loc.count = AsyncMock(return_value=0)
        desc_loc.first = MagicMock()
        desc_loc.first.text_content = AsyncMock(return_value="")

        criteria_loc = MagicMock()
        criteria_loc.count = AsyncMock(return_value=0)

        salary_loc = MagicMock()
        salary_loc.count = AsyncMock(return_value=1)
        salary_loc.first = MagicMock()
        salary_loc.first.text_content = AsyncMock(return_value="$120,000 - $160,000")

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            if selector == SELECTORS["detail_description"]:
                return desc_loc
            if selector == SELECTORS["detail_criteria"]:
                return criteria_loc
            if selector == SELECTORS["detail_salary"]:
                return salary_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        assert result["salary_range"] == "$120,000 - $160,000"

    async def test_parses_about_the_job_h2_primary_path(self, scraper):
        """Verify description is extracted via h2 'About the job' grandparent."""
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=False)

        # h2:has-text('About the job') locator — primary path
        about_h2_loc = MagicMock()
        about_h2_loc.count = AsyncMock(return_value=1)
        container_loc = MagicMock()
        container_loc.text_content = AsyncMock(
            return_value="About the job\nWe are hiring a senior Python developer."
        )
        about_h2_loc.first = MagicMock()
        about_h2_loc.first.locator = MagicMock(return_value=container_loc)

        criteria_loc = MagicMock()
        criteria_loc.count = AsyncMock(return_value=0)

        salary_loc = MagicMock()
        salary_loc.count = AsyncMock(return_value=0)

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            if selector == "h2:has-text('About the job')":
                return about_h2_loc
            if selector == SELECTORS["detail_criteria"]:
                return criteria_loc
            if selector == SELECTORS["detail_salary"]:
                return salary_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        assert result["description"] == "We are hiring a senior Python developer."

    async def test_handles_missing_elements(self, scraper):
        page = MagicMock()

        show_more_loc = _make_show_more_loc(visible=False)

        empty_loc = MagicMock()
        empty_loc.count = AsyncMock(return_value=0)
        empty_loc.first = MagicMock()
        empty_loc.first.text_content = AsyncMock(return_value="")

        def locator_side_effect(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["detail_show_more"]:
                return show_more_loc
            return empty_loc

        page.locator = MagicMock(side_effect=locator_side_effect)

        result = await scraper._parse_job_detail_page(page)
        assert result["description"] == ""
        assert result["salary_range"] is None
        assert result["job_type"] is None
        assert result["experience_level"] is None


# ---------------------------------------------------------------------------
# Dedup logic tests
# ---------------------------------------------------------------------------


class TestDedupLogic:
    @pytest.fixture
    def scraper(self):
        return LinkedInJobScraper(_make_mock_browser(), _make_mock_settings())

    def test_seen_ids_starts_empty(self, scraper):
        assert len(scraper._seen_job_ids) == 0

    def test_reset_seen_clears(self, scraper):
        scraper._seen_job_ids.add("123")
        scraper._seen_job_ids.add("456")
        scraper.reset_seen()
        assert len(scraper._seen_job_ids) == 0

    async def test_dedup_skips_already_seen(self, scraper):
        """scrape_search_results should skip jobs with IDs already in _seen_job_ids."""
        scraper._seen_job_ids.add("1234567890")

        # Build a page mock with one card that has the duplicate job ID
        card = MagicMock()
        card.get_attribute = AsyncMock(return_value="1234567890")
        title_loc = MagicMock()
        title_loc.first = MagicMock()
        title_loc.first.text_content = AsyncMock(return_value="Developer")
        title_loc.first.get_attribute = AsyncMock(
            side_effect=lambda attr: "Developer" if attr == "aria-label" else "/jobs/view/1234567890/"
        )

        default_loc = MagicMock()
        default_loc.count = AsyncMock(return_value=0)
        default_loc.first = MagicMock()
        default_loc.first.text_content = AsyncMock(return_value="Company")

        easy_loc = MagicMock()
        easy_loc.count = AsyncMock(return_value=0)

        posted_loc = MagicMock()
        posted_loc.count = AsyncMock(return_value=0)
        posted_loc.first = MagicMock()
        posted_loc.first.text_content = AsyncMock(return_value="")

        def card_locator(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["job_card_title"]:
                return title_loc
            if selector == SELECTORS["job_card_easy_apply"]:
                return easy_loc
            if selector == SELECTORS["job_card_posted"]:
                return posted_loc
            return default_loc

        card.locator = MagicMock(side_effect=card_locator)

        # Page mock
        no_results_loc = MagicMock()
        no_results_loc.count = AsyncMock(return_value=0)

        cards_loc = MagicMock()
        cards_loc.count = AsyncMock(return_value=1)
        cards_loc.nth = MagicMock(return_value=card)

        page = AsyncMock()
        page.goto = AsyncMock()

        call_count = 0

        def page_locator(selector):
            nonlocal call_count
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["no_results"]:
                return no_results_loc
            if selector == SELECTORS["job_card"]:
                # Return cards only on first call, then empty to stop pagination
                call_count += 1
                if call_count <= 1:
                    return cards_loc
                empty = MagicMock()
                empty.count = AsyncMock(return_value=0)
                return empty
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=page_locator)
        scraper.browser.page = page

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="python", max_jobs=10)
        results = await scraper.scrape_search_results(params)

        # The duplicate should have been skipped
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Max jobs limit tests
# ---------------------------------------------------------------------------


class TestMaxJobsLimit:
    async def test_stops_at_max_jobs(self):
        """Pagination should stop once max_jobs is reached."""
        settings = _make_mock_settings()
        browser = _make_mock_browser()

        scraper = LinkedInJobScraper(browser, settings)

        # Create 3 distinct job cards
        def make_card(job_id):
            card = MagicMock()
            card.get_attribute = AsyncMock(return_value=str(job_id))
            title_loc = MagicMock()
            title_loc.first = MagicMock()
            title_loc.first.text_content = AsyncMock(return_value=f"Job {job_id}")
            title_loc.first.get_attribute = AsyncMock(
                side_effect=lambda attr: (
                    f"Job {job_id}" if attr == "aria-label" else f"/jobs/view/{job_id}/"
                )
            )

            default_loc = MagicMock()
            default_loc.count = AsyncMock(return_value=0)
            default_loc.first = MagicMock()
            default_loc.first.text_content = AsyncMock(return_value="Company")

            easy_loc = MagicMock()
            easy_loc.count = AsyncMock(return_value=0)

            posted_loc = MagicMock()
            posted_loc.count = AsyncMock(return_value=0)
            posted_loc.first = MagicMock()
            posted_loc.first.text_content = AsyncMock(return_value="")

            def card_locator(selector):
                from src.services.linkedin_scraper import SELECTORS

                if selector == SELECTORS["job_card_title"]:
                    return title_loc
                if selector == SELECTORS["job_card_easy_apply"]:
                    return easy_loc
                if selector == SELECTORS["job_card_posted"]:
                    return posted_loc
                return default_loc

            card.locator = MagicMock(side_effect=card_locator)
            return card

        cards = [make_card(f"10000000{i}") for i in range(3)]

        no_results_loc = MagicMock()
        no_results_loc.count = AsyncMock(return_value=0)

        cards_loc = MagicMock()
        cards_loc.count = AsyncMock(return_value=3)
        cards_loc.nth = MagicMock(side_effect=lambda i: cards[i])

        page = AsyncMock()
        page.goto = AsyncMock()

        def page_locator(selector):
            from src.services.linkedin_scraper import SELECTORS

            if selector == SELECTORS["no_results"]:
                return no_results_loc
            if selector == SELECTORS["job_card"]:
                return cards_loc
            return MagicMock(count=AsyncMock(return_value=0))

        page.locator = MagicMock(side_effect=page_locator)
        browser.page = page

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="python", max_jobs=2)
        results = await scraper.scrape_search_results(params)

        # Should stop at 2 even though 3 cards available
        assert len(results) == 2


# ---------------------------------------------------------------------------
# scrape_and_enrich tests
# ---------------------------------------------------------------------------


class TestScrapeAndEnrich:
    async def test_enriches_jobs_with_details(self):
        settings = _make_mock_settings()
        browser = _make_mock_browser()
        scraper = LinkedInJobScraper(browser, settings)

        # Mock scrape_search_results to return ScrapedJob instances
        scraper.scrape_search_results = AsyncMock(
            return_value=[
                ScrapedJob(
                    job_id="111",
                    title="Dev",
                    company="Co",
                    location="NYC",
                    url="https://www.linkedin.com/jobs/view/111/",
                ),
                ScrapedJob(
                    job_id="222",
                    title="Eng",
                    company="Co",
                    location="SF",
                    url="https://www.linkedin.com/jobs/view/222/",
                ),
            ]
        )

        # Mock scrape_job_details
        scraper.scrape_job_details = AsyncMock(
            side_effect=[
                {"description": "Detail for 111", "salary_range": "$100k"},
                {"description": "Detail for 222", "salary_range": None},
            ]
        )

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="python")
        results = await scraper.scrape_and_enrich(params)

        assert len(results) == 2
        assert results[0].description == "Detail for 111"
        assert results[0].salary_range == "$100k"
        assert results[1].description == "Detail for 222"

    async def test_handles_enrichment_failure(self):
        settings = _make_mock_settings()
        browser = _make_mock_browser()
        scraper = LinkedInJobScraper(browser, settings)

        scraper.scrape_search_results = AsyncMock(
            return_value=[
                ScrapedJob(
                    job_id="333",
                    title="Dev",
                    company="Co",
                    location="NYC",
                    url="https://www.linkedin.com/jobs/view/333/",
                ),
            ]
        )
        scraper.scrape_job_details = AsyncMock(side_effect=Exception("Page crashed"))

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="python")
        results = await scraper.scrape_and_enrich(params)

        # Should still include the job even if enrichment failed
        assert len(results) == 1
        assert results[0].job_id == "333"

    async def test_skips_enrichment_without_url(self):
        settings = _make_mock_settings()
        browser = _make_mock_browser()
        scraper = LinkedInJobScraper(browser, settings)

        scraper.scrape_search_results = AsyncMock(
            return_value=[
                ScrapedJob(job_id="444", title="Dev", company="Co", location="NYC", url=""),
            ]
        )
        scraper.scrape_job_details = AsyncMock()

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="python")
        results = await scraper.scrape_and_enrich(params)

        assert len(results) == 1
        scraper.scrape_job_details.assert_not_awaited()


# ---------------------------------------------------------------------------
# scrape_search_results no-results tests
# ---------------------------------------------------------------------------


class TestNoResults:
    async def test_returns_empty_on_no_results(self):
        settings = _make_mock_settings()
        browser = _make_mock_browser()
        scraper = LinkedInJobScraper(browser, settings)

        no_results_loc = MagicMock()
        no_results_loc.count = AsyncMock(return_value=1)  # No results banner present

        page = AsyncMock()
        page.goto = AsyncMock()
        page.locator = MagicMock(return_value=no_results_loc)
        browser.page = page

        from src.services.linkedin_search import LinkedInSearchParams

        params = LinkedInSearchParams(keywords="nonexistent")
        results = await scraper.scrape_search_results(params)

        assert results == []
