"""Tests for LinkedIn search URL builder and filter models."""

import pytest
from urllib.parse import urlparse, parse_qs

from src.services.linkedin_search import (
    LinkedInSearchParams,
    LinkedInSearchURLBuilder,
    LINKEDIN_JOBS_SEARCH_BASE,
)


def _parse_url(url: str) -> dict[str, list[str]]:
    """Parse URL and return query parameters as dict."""
    parsed = urlparse(url)
    return parse_qs(parsed.query)


class TestLinkedInSearchParams:
    """Test LinkedInSearchParams model."""

    def test_defaults(self):
        params = LinkedInSearchParams()
        assert params.keywords == ""
        assert params.location == ""
        assert params.remote_filter is None
        assert params.date_posted is None
        assert params.experience_level is None
        assert params.job_type is None
        assert params.easy_apply_only is False
        assert params.max_jobs == 50

    def test_custom_values(self):
        params = LinkedInSearchParams(
            keywords="python developer",
            location="Berlin",
            remote_filter="remote",
            date_posted="week",
            experience_level=["mid-senior", "director"],
            job_type=["full-time"],
            easy_apply_only=True,
            max_jobs=25,
        )
        assert params.keywords == "python developer"
        assert params.location == "Berlin"
        assert params.remote_filter == "remote"
        assert params.experience_level == ["mid-senior", "director"]
        assert params.max_jobs == 25


class TestLinkedInSearchURLBuilder:
    """Test LinkedInSearchURLBuilder.build_url."""

    def test_empty_params_returns_base_url(self):
        params = LinkedInSearchParams()
        url = LinkedInSearchURLBuilder.build_url(params)
        assert url.startswith(LINKEDIN_JOBS_SEARCH_BASE)
        qs = _parse_url(url)
        assert qs == {}

    def test_keywords_only(self):
        params = LinkedInSearchParams(keywords="python engineer")
        url = LinkedInSearchURLBuilder.build_url(params)
        qs = _parse_url(url)
        assert qs["keywords"] == ["python engineer"]

    def test_location_only(self):
        params = LinkedInSearchParams(location="New York, NY")
        url = LinkedInSearchURLBuilder.build_url(params)
        qs = _parse_url(url)
        assert qs["location"] == ["New York, NY"]

    def test_keywords_and_location(self):
        params = LinkedInSearchParams(keywords="data scientist", location="London")
        url = LinkedInSearchURLBuilder.build_url(params)
        qs = _parse_url(url)
        assert qs["keywords"] == ["data scientist"]
        assert qs["location"] == ["London"]

    def test_date_posted_24h(self):
        params = LinkedInSearchParams(date_posted="24h")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_TPR"] == ["r86400"]

    def test_date_posted_week(self):
        params = LinkedInSearchParams(date_posted="week")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_TPR"] == ["r604800"]

    def test_date_posted_month(self):
        params = LinkedInSearchParams(date_posted="month")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_TPR"] == ["r2592000"]

    def test_date_posted_invalid_ignored(self):
        params = LinkedInSearchParams(date_posted="year")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert "f_TPR" not in qs

    def test_remote_filter(self):
        for label, code in [("remote", "2"), ("on-site", "1"), ("hybrid", "3")]:
            params = LinkedInSearchParams(remote_filter=label)
            qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
            assert qs["f_WT"] == [code], f"Failed for {label}"

    def test_remote_filter_invalid_ignored(self):
        params = LinkedInSearchParams(remote_filter="flex")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert "f_WT" not in qs

    def test_single_experience_level(self):
        params = LinkedInSearchParams(experience_level=["entry"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_E"] == ["2"]

    def test_multiple_experience_levels(self):
        params = LinkedInSearchParams(experience_level=["entry", "mid-senior", "executive"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_E"] == ["2,4,6"]

    def test_experience_level_invalid_filtered(self):
        params = LinkedInSearchParams(experience_level=["entry", "bogus", "director"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_E"] == ["2,5"]

    def test_experience_level_all_invalid(self):
        params = LinkedInSearchParams(experience_level=["bogus"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert "f_E" not in qs

    def test_single_job_type(self):
        params = LinkedInSearchParams(job_type=["full-time"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_JT"] == ["F"]

    def test_multiple_job_types(self):
        params = LinkedInSearchParams(job_type=["full-time", "contract", "internship"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_JT"] == ["F,C,I"]

    def test_job_type_invalid_filtered(self):
        params = LinkedInSearchParams(job_type=["full-time", "freelance"])
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_JT"] == ["F"]

    def test_easy_apply_true(self):
        params = LinkedInSearchParams(easy_apply_only=True)
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert qs["f_AL"] == ["true"]

    def test_easy_apply_false_omitted(self):
        params = LinkedInSearchParams(easy_apply_only=False)
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params))
        assert "f_AL" not in qs

    def test_pagination_page_0(self):
        params = LinkedInSearchParams(keywords="test")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params, page=0))
        assert "start" not in qs

    def test_pagination_page_1(self):
        params = LinkedInSearchParams(keywords="test")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params, page=1))
        assert qs["start"] == ["25"]

    def test_pagination_page_3(self):
        params = LinkedInSearchParams(keywords="test")
        qs = _parse_url(LinkedInSearchURLBuilder.build_url(params, page=3))
        assert qs["start"] == ["75"]

    def test_all_filters_combined(self):
        params = LinkedInSearchParams(
            keywords="software engineer",
            location="San Francisco",
            remote_filter="remote",
            date_posted="24h",
            experience_level=["mid-senior"],
            job_type=["full-time"],
            easy_apply_only=True,
        )
        url = LinkedInSearchURLBuilder.build_url(params, page=2)
        qs = _parse_url(url)
        assert qs["keywords"] == ["software engineer"]
        assert qs["location"] == ["San Francisco"]
        assert qs["f_WT"] == ["2"]
        assert qs["f_TPR"] == ["r86400"]
        assert qs["f_E"] == ["4"]
        assert qs["f_JT"] == ["F"]
        assert qs["f_AL"] == ["true"]
        assert qs["start"] == ["50"]

    def test_url_starts_with_base(self):
        params = LinkedInSearchParams(keywords="test")
        url = LinkedInSearchURLBuilder.build_url(params)
        assert url.startswith(LINKEDIN_JOBS_SEARCH_BASE)


