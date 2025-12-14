"""Job posting data models"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


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


class JobFilter(BaseModel):
    """Model for job search filters"""
    keywords: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    remote_only: bool = True
    experience_levels: List[str] = Field(default_factory=list)
    job_types: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)


class JobEvaluation(BaseModel):
    """Model for LLM job evaluation result"""
    job_id: str
    is_suitable: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    evaluated_at: datetime = Field(default_factory=datetime.now)
