"""API response models for the PDF CV extraction endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CVExtractionStartResponse(BaseModel):
    """Returned from POST /api/users/me/master-cv/extract."""

    extraction_id: str
    status: Literal["pending"]


class CVExtractionStatusResponse(BaseModel):
    """Returned from GET /api/users/me/master-cv/extract/{id}."""

    extraction_id: str
    status: Literal["pending", "running", "completed", "failed"]
    result_json: dict | None = None
    validation_errors: list[str] = Field(default_factory=list)
    error_message: str | None = None
