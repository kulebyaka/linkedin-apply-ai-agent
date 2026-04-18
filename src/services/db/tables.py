"""Piccolo ORM table definitions for job persistence and user management.

This module defines the database schema for storing job records,
CV composition attempts, users, and magic link tokens.
"""

from piccolo.columns import (
    JSON,
    Boolean,
    Integer,
    Text,
    Timestamptz,
    Varchar,
)
from piccolo.table import Table


class UserTable(Table, tablename="user"):
    """Piccolo ORM model for user accounts.

    Stores user profile, master CV, and LinkedIn search preferences.
    """

    id = Varchar(length=36, primary_key=True)  # UUID
    email = Varchar(length=255, unique=True, index=True)
    display_name = Varchar(length=100)
    master_cv_json = JSON(null=True)
    search_preferences = JSON(null=True)  # Serialized LinkedInSearchParams
    filter_preferences = JSON(null=True)  # Serialized UserFilterPreferences
    model_preferences = JSON(null=True)  # Serialized UserModelPreferences
    created_at = Timestamptz(index=True)
    updated_at = Timestamptz()


class MagicLinkTable(Table, tablename="magic_link"):
    """Piccolo ORM model for magic link authentication tokens.

    Stores short-lived tokens for passwordless email authentication.
    """

    token = Varchar(length=64, primary_key=True)
    email = Varchar(length=255, index=True)
    expires_at = Timestamptz()
    used = Boolean(default=False)


class Job(Table):
    """Piccolo ORM model for job persistence.

    Maps to the SQLite database table storing job records.
    Uses JSON columns for complex nested data (job_posting, etc.).
    """

    # Primary key
    job_id = Varchar(length=80, primary_key=True)  # UUID or composite key (linkedin_id:user_id)

    # Owner
    user_id = Varchar(length=36, index=True)

    # Metadata
    source = Varchar(length=20)  # url, manual, linkedin
    mode = Varchar(length=10)  # mvp, full
    status = Varchar(length=30, index=True)  # queued, pending, applied, etc.

    # Job data (JSON TEXT columns)
    job_posting = JSON(null=True)
    raw_input = JSON(null=True)

    # Denormalized quick-access to latest CV attempt
    current_cv_json = JSON(null=True)
    current_pdf_path = Varchar(length=500, null=True)

    # Application data
    application_url = Varchar(length=500, null=True, index=True)

    # Filter result (LLM job evaluation)
    filter_result = JSON(null=True)

    # Error tracking
    error_message = Text(null=True)

    # Timestamps
    created_at = Timestamptz(index=True)  # Index for sorting
    updated_at = Timestamptz()


class CVAttemptTable(Table, tablename="cv_attempt"):
    """Piccolo ORM model for CV composition attempt history.

    Tracks each CV composition (initial + retries) for a job.
    """

    # Composite identity: job_id + attempt_number
    job_id = Varchar(length=80, index=True)
    attempt_number = Integer()

    # Owner
    user_id = Varchar(length=36, index=True)

    # Attempt data
    user_feedback = Text(null=True)
    cv_json = JSON()
    pdf_path = Varchar(length=500, null=True)

    # Timestamp
    created_at = Timestamptz()
