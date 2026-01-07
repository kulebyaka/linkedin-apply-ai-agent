"""Piccolo ORM table definitions for job persistence.

This module defines the database schema for storing job records.
"""

from piccolo.table import Table
from piccolo.columns import (
    Varchar,
    JSON,
    Integer,
    Timestamptz,
    Text,
)


class Job(Table):
    """Piccolo ORM model for job persistence.

    Maps to the SQLite database table storing job records.
    Uses JSON columns for complex nested data (job_posting, cv_json, etc.).
    """

    # Primary key
    job_id = Varchar(length=36, primary_key=True)  # UUID

    # Metadata
    source = Varchar(length=20)  # url, manual, linkedin
    mode = Varchar(length=10)  # mvp, full
    status = Varchar(length=30, index=True)  # queued, pending, applied, etc.

    # Job data (JSON TEXT columns)
    job_posting = JSON(null=True)
    raw_input = JSON(null=True)

    # CV data
    cv_json = JSON(null=True)
    pdf_path = Varchar(length=500, null=True)

    # Application data
    application_url = Varchar(length=500, null=True, index=True)
    application_type = Varchar(length=20, null=True)  # deep_agent, linkedin, manual
    application_result = JSON(null=True)

    # HITL data
    user_feedback = Text(null=True)
    retry_count = Integer(default=0)

    # Error tracking
    error_message = Text(null=True)

    # Timestamps
    created_at = Timestamptz(index=True)  # Index for sorting
    updated_at = Timestamptz()
    applied_at = Timestamptz(null=True)
