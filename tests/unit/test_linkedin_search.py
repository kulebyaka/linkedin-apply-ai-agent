"""Tests for LinkedIn search URL builder and filter models."""

from urllib.parse import parse_qs, urlparse

from src.services.linkedin.linkedin_search import (
    LINKEDIN_JOBS_SEARCH_BASE,
    LinkedInSearchParams,
    LinkedInSearchURLBuilder,
    normalize_keywords,
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


class TestNormalizeKeywords:
    """Test comma-list -> LinkedIn boolean-OR translation."""

    def test_comma_list_becomes_or_with_quoted_phrases(self):
        result = normalize_keywords(
            "Junior Accountant, Finance Assistant, Billing Specialist"
        )
        assert result == (
            '"Junior Accountant" OR "Finance Assistant" OR "Billing Specialist"'
        )

    def test_single_word_terms_are_not_quoted(self):
        assert normalize_keywords("python, java, rust") == "python OR java OR rust"

    def test_mixed_single_and_multiword(self):
        assert normalize_keywords("AP, Accounts Payable") == 'AP OR "Accounts Payable"'

    def test_single_term_without_comma_is_unchanged(self):
        assert normalize_keywords("Junior Accountant") == "Junior Accountant"
        assert normalize_keywords("python") == "python"

    def test_existing_boolean_query_is_untouched(self):
        q = '"Junior Accountant" OR "Finance Assistant"'
        assert normalize_keywords(q) == q

    def test_boolean_operators_without_quotes_untouched(self):
        assert normalize_keywords("python AND django") == "python AND django"
        assert normalize_keywords("java NOT scala") == "java NOT scala"

    def test_parentheses_untouched(self):
        q = "(accountant OR analyst) AND finance"
        assert normalize_keywords(q) == q

    def test_lowercase_or_between_words_is_not_treated_as_operator(self):
        # No comma, no uppercase operator -> passed through verbatim.
        assert normalize_keywords("sales or marketing") == "sales or marketing"

    def test_whitespace_and_empty_segments_are_trimmed(self):
        assert normalize_keywords("  a ,  , b  ,") == "a OR b"

    def test_duplicate_terms_are_deduped_case_insensitively(self):
        assert (
            normalize_keywords("Accountant, accountant, ACCOUNTANT")
            == "Accountant"
        )

    def test_empty_and_whitespace_only(self):
        assert normalize_keywords("") == ""
        assert normalize_keywords("   ") == ""

    def test_build_url_applies_normalization(self):
        params = LinkedInSearchParams(keywords="Junior Accountant, Finance Assistant")
        url = LinkedInSearchURLBuilder.build_url(params)
        # parse_qs decodes the percent-encoding back to the raw boolean string.
        keywords = _parse_url(url)["keywords"][0]
        assert keywords == '"Junior Accountant" OR "Finance Assistant"'


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


