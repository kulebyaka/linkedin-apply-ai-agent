"""LinkedIn search URL builder and filter models.

Constructs LinkedIn job search URLs with proper query parameters
for keywords, location, date posted, remote filter, experience level,
job type, and Easy Apply filtering.
"""

from urllib.parse import urlencode

from pydantic import BaseModel

# LinkedIn query parameter mappings
_DATE_POSTED_MAP = {
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
}

_REMOTE_FILTER_MAP = {
    "on-site": "1",
    "remote": "2",
    "hybrid": "3",
}

_EXPERIENCE_LEVEL_MAP = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}

_JOB_TYPE_MAP = {
    "full-time": "F",
    "part-time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
    "other": "O",
}

LINKEDIN_JOBS_SEARCH_BASE = "https://www.linkedin.com/jobs/search/"


class LinkedInSearchParams(BaseModel):
    """Encapsulates all LinkedIn job search filters."""

    keywords: str = ""
    location: str = ""
    remote_filter: str | None = None  # "remote", "on-site", "hybrid"
    date_posted: str | None = None  # "24h", "week", "month"
    experience_level: list[str] | None = None  # "entry", "associate", "mid-senior", etc.
    job_type: list[str] | None = None  # "full-time", "part-time", "contract", etc.
    easy_apply_only: bool = False
    max_jobs: int = 50


class LinkedInSearchURLBuilder:
    """Builds LinkedIn job search URLs with encoded query parameters."""

    @staticmethod
    def build_url(params: LinkedInSearchParams, page: int = 0) -> str:
        """Construct a LinkedIn job search URL from search parameters.

        Args:
            params: Search filter parameters.
            page: Zero-based page index (each page = 25 results).

        Returns:
            Fully encoded LinkedIn search URL string.
        """
        query: dict[str, str] = {}

        if params.keywords:
            query["keywords"] = params.keywords

        if params.location:
            query["location"] = params.location

        if params.date_posted and params.date_posted in _DATE_POSTED_MAP:
            query["f_TPR"] = _DATE_POSTED_MAP[params.date_posted]

        if params.remote_filter and params.remote_filter in _REMOTE_FILTER_MAP:
            query["f_WT"] = _REMOTE_FILTER_MAP[params.remote_filter]

        if params.experience_level:
            codes = [
                _EXPERIENCE_LEVEL_MAP[lvl]
                for lvl in params.experience_level
                if lvl in _EXPERIENCE_LEVEL_MAP
            ]
            if codes:
                query["f_E"] = ",".join(codes)

        if params.job_type:
            codes = [
                _JOB_TYPE_MAP[jt]
                for jt in params.job_type
                if jt in _JOB_TYPE_MAP
            ]
            if codes:
                query["f_JT"] = ",".join(codes)

        if params.easy_apply_only:
            query["f_AL"] = "true"

        if page > 0:
            query["start"] = str(page * 25)

        return LINKEDIN_JOBS_SEARCH_BASE + "?" + urlencode(query)

    @classmethod
    def build_url_from_settings(cls, settings, page: int = 0) -> str:
        """Build URL directly from application Settings object.

        Args:
            settings: Application Settings instance.
            page: Zero-based page index.

        Returns:
            LinkedIn search URL string.
        """
        params = LinkedInSearchParams(
            keywords=settings.linkedin_search_keywords,
            location=settings.linkedin_search_location,
            remote_filter=settings.linkedin_search_remote_filter,
            date_posted=settings.linkedin_search_date_posted,
            experience_level=settings.linkedin_search_experience_level,
            job_type=settings.linkedin_search_job_type,
            easy_apply_only=settings.linkedin_search_easy_apply_only,
            max_jobs=settings.linkedin_search_max_jobs,
        )
        return cls.build_url(params, page)
