"""Tests for Pydantic models"""

import pytest
from datetime import date, datetime
from src.models.cv import CV, ContactInfo, Experience, Education, Skill
from src.models.job import JobPosting, JobFilter, JobEvaluation
from src.models.application import ApplicationStatus, UserApproval


class TestCVModels:
    """Test CV-related models"""

    def test_contact_info(self):
        """Test ContactInfo model"""
        contact = ContactInfo(
            full_name="John Doe",
            email="john@example.com",
            phone="+1234567890",
            location="San Francisco, CA"
        )
        assert contact.full_name == "John Doe"
        assert contact.email == "john@example.com"

    def test_experience(self):
        """Test Experience model"""
        exp = Experience(
            company="Tech Corp",
            position="Senior Engineer",
            start_date=date(2020, 1, 1),
            is_current=True,
            description="Building awesome software",
            achievements=["Led team of 5", "Shipped product X"]
        )
        assert exp.is_current is True
        assert exp.end_date is None
        assert len(exp.achievements) == 2


class TestJobModels:
    """Test job-related models"""

    def test_job_posting(self):
        """Test JobPosting model"""
        job = JobPosting(
            id="123",
            title="Software Engineer",
            company="Tech Corp",
            location="Remote",
            description="Great opportunity",
            is_remote=True,
            url="https://linkedin.com/jobs/123"
        )
        assert job.is_remote is True
        assert job.title == "Software Engineer"

    def test_job_filter(self):
        """Test JobFilter model"""
        filters = JobFilter(
            keywords=["python", "engineer"],
            remote_only=True,
            experience_levels=["senior"]
        )
        assert "python" in filters.keywords
        assert filters.remote_only is True


class TestApplicationModels:
    """Test application-related models"""

    def test_application_status(self):
        """Test ApplicationStatus model"""
        status = ApplicationStatus(
            job_id="123",
            status="pending_approval"
        )
        assert status.status == "pending_approval"
        assert isinstance(status.created_at, datetime)

    def test_user_approval(self):
        """Test UserApproval model"""
        approval = UserApproval(
            job_id="123",
            decision="approved",
            feedback="Looks great!"
        )
        assert approval.decision == "approved"
        assert approval.feedback == "Looks great!"
