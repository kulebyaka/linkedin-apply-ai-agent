"""Tests for Pydantic models"""

from datetime import date

from src.models.cv import ContactInfo, Experience
from src.models.job import JobPosting


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
