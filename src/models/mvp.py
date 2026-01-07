"""Pydantic models for MVP CV generation API"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class JobDescriptionInput(BaseModel):
    """User input for CV generation"""
    title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    description: str = Field(..., description="Full job description")
    requirements: Optional[str] = Field(None, description="Job requirements section")
    template_name: Optional[str] = Field(None, description="CV template: modern, compact, classic, minimal, profile-card")
    llm_provider: Optional[Literal["openai", "anthropic"]] = Field(None, description="LLM provider: openai, anthropic")
    llm_model: Optional[str] = Field(None, description="LLM model name (e.g., gpt-4.1-nano, claude-haiku-4.5)")


class CVGenerationResponse(BaseModel):
    """Immediate response after job submission"""
    job_id: str
    status: Literal["queued"]
    message: str


class CVGenerationStatus(BaseModel):
    """Status response for polling"""
    job_id: str
    status: Literal[
        "queued",
        "extracting",
        "job_extracted",
        "composing_cv",
        "cv_composed",
        "generating_pdf",
        "pdf_generated",
        "saving",
        "completed",
        "failed",
    ]
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    pdf_path: Optional[str] = None
