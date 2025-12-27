"""Job posting data models"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class JobPosting(BaseModel):
    """Model for a job posting"""
    id: str
    title: str
    company: str
    location: str
    description: str
    requirements: Optional[str] = None
    salary_range: Optional[str] = None
    is_remote: bool = False
    experience_level: Optional[str] = None
    job_type: Optional[str] = None  # full-time, part-time, contract
    posted_date: Optional[datetime] = None
    url: str
    raw_data: Optional[dict] = None
