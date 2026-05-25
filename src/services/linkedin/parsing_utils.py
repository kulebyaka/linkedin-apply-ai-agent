"""Pure parsing helpers shared by the scraper and detail parser."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Relative time patterns used by LinkedIn (e.g. "2 days ago", "1 week ago")
_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago", re.IGNORECASE
)

_TIME_UNIT_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
}


def _parse_relative_time(text: str) -> datetime | None:
    """Parse LinkedIn relative time strings like '2 days ago' into datetime."""
    match = _RELATIVE_TIME_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = _TIME_UNIT_SECONDS.get(unit, 0) * amount
    return datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)


def _extract_job_id_from_url(url: str) -> str | None:
    """Extract LinkedIn job ID from a job URL or href.

    Authenticated SPA: /jobs/view/<id>/ or ?currentJobId=<id>.
    Public guest JSERP: /jobs/view/<slug>-<id>?... (trailing digits after slug).
    """
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/jobs/view/[^?/]*?-(\d+)(?=[?/]|$)", url)
    if m:
        return m.group(1)
    return None


def _extract_job_id_from_urn(urn: str | None) -> str | None:
    """Extract LinkedIn job ID from `urn:li:jobPosting:<id>` (guest layout)."""
    if not urn:
        return None
    m = re.search(r"urn:li:jobPosting:(\d+)", urn)
    return m.group(1) if m else None
