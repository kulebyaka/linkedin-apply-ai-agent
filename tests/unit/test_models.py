"""Tests for Pydantic models"""

from datetime import date

from src.models.cv import ContactInfo, Experience


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


