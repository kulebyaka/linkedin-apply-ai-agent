"""Unit tests for PDF Generator service"""

import pytest
from pathlib import Path
import json
from src.services.pdf_generator import PDFGenerator


class TestPDFGenerator:
    """Test cases for PDFGenerator class"""

    def test_pdf_generator_initialization(self):
        """Test PDFGenerator initializes with valid template"""
        generator = PDFGenerator(template_name="modern")
        assert generator.template_name == "modern"
        assert generator.template_dir == Path("src/templates/cv")

    def test_pdf_generator_invalid_template(self):
        """Test PDFGenerator raises error for invalid template"""
        with pytest.raises(ValueError, match="Template.*not found"):
            PDFGenerator(template_name="nonexistent")

    def test_format_date_string(self):
        """Test date filter formats date strings correctly"""
        assert PDFGenerator._format_date("2020-03-01") == "Mar 2020"
        assert PDFGenerator._format_date("2020-12-31") == "Dec 2020"
        assert PDFGenerator._format_date("2024-01-15") == "Jan 2024"

    def test_format_date_none(self):
        """Test date filter handles None as 'Present'"""
        assert PDFGenerator._format_date(None) == "Present"

    def test_format_date_present_string(self):
        """Test date filter handles 'present' strings"""
        assert PDFGenerator._format_date("present") == "Present"
        assert PDFGenerator._format_date("Present") == "Present"
        assert PDFGenerator._format_date("current") == "Present"

    def test_cv_to_html(self):
        """Test CV JSON renders to HTML correctly"""
        generator = PDFGenerator()
        cv_json = {
            "contact": {
                "full_name": "John Doe",
                "email": "john.doe@example.com",
                "phone": "+1-234-567-8900",
                "location": "San Francisco, CA"
            },
            "summary": "Experienced software engineer",
            "experiences": [],
            "education": [],
            "skills": [],
            "projects": [],
            "certifications": [],
            "languages": []
        }

        html = generator._cv_to_html(cv_json)

        assert "John Doe" in html
        assert "john.doe@example.com" in html
        assert "+1-234-567-8900" in html
        assert "Experienced software engineer" in html

    def test_generate_pdf_creates_file(self, tmp_path, sample_cv_json):
        """Test PDF file is created at specified path"""
        generator = PDFGenerator()
        output_path = tmp_path / "test.pdf"

        result_path = generator.generate_pdf(sample_cv_json, output_path)

        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0
        assert Path(result_path).suffix == ".pdf"

    def test_generate_pdf_with_metadata(self, tmp_path, sample_cv_json):
        """Test PDF is generated with custom metadata"""
        generator = PDFGenerator()
        output_path = tmp_path / "test_with_metadata.pdf"

        custom_metadata = {
            "keywords": "Python, Software Engineering"
        }

        result_path = generator.generate_pdf(
            sample_cv_json,
            output_path,
            metadata=custom_metadata
        )

        assert Path(result_path).exists()

    def test_build_metadata(self):
        """Test PDF metadata is built correctly"""
        generator = PDFGenerator()
        cv_json = {
            "contact": {
                "full_name": "Jane Smith"
            }
        }

        metadata = generator._build_metadata(cv_json, None)

        assert metadata["title"] == "Jane Smith - Resume"
        assert metadata["author"] == "Jane Smith"
        assert metadata["subject"] == "Professional Resume"
        assert metadata["creator"] == "LinkedIn Job Application Agent"

    def test_build_metadata_with_custom(self):
        """Test custom metadata overrides defaults"""
        generator = PDFGenerator()
        cv_json = {
            "contact": {
                "full_name": "Jane Smith"
            }
        }
        custom_metadata = {"title": "Custom Title"}

        metadata = generator._build_metadata(cv_json, custom_metadata)

        assert metadata["title"] == "Custom Title"
        assert metadata["author"] == "Jane Smith"

    def test_generate_pdf_creates_output_directory(self, tmp_path, sample_cv_json):
        """Test PDF generation creates output directory if it doesn't exist"""
        generator = PDFGenerator()
        nested_path = tmp_path / "nested" / "directory" / "test.pdf"

        result_path = generator.generate_pdf(sample_cv_json, nested_path)

        assert Path(result_path).exists()
        assert nested_path.parent.exists()


# Fixtures

@pytest.fixture
def sample_cv_json():
    """Sample CV JSON for testing"""
    return {
        "contact": {
            "full_name": "John Doe",
            "email": "john.doe@example.com",
            "phone": "+1-234-567-8900",
            "location": "San Francisco, CA",
            "linkedin_url": "https://linkedin.com/in/johndoe",
            "github_url": "https://github.com/johndoe",
            "portfolio_url": "https://johndoe.com"
        },
        "summary": "Experienced software engineer with 10+ years in backend development.",
        "experiences": [
            {
                "company": "Tech Corp",
                "position": "Senior Software Engineer",
                "start_date": "2020-03-01",
                "end_date": None,
                "is_current": True,
                "location": "San Francisco, CA (Remote)",
                "description": "Lead engineer for cloud-native microservices.",
                "achievements": [
                    "Reduced API latency by 40%",
                    "Led team of 5 engineers"
                ],
                "technologies": ["Python", "Go", "AWS", "Kubernetes"]
            }
        ],
        "education": [
            {
                "institution": "University of California, Berkeley",
                "degree": "Bachelor of Science",
                "field_of_study": "Computer Science",
                "start_date": "2006-09-01",
                "end_date": "2010-05-31",
                "gpa": "3.8",
                "achievements": ["Dean's List", "CS Honor Society"]
            }
        ],
        "skills": [
            {"name": "Python", "category": "Programming Languages", "proficiency": "Expert"},
            {"name": "AWS", "category": "Cloud & DevOps", "proficiency": "Advanced"}
        ],
        "projects": [
            {
                "name": "Open Source Library",
                "description": "Python library for async task processing",
                "url": "https://github.com/johndoe/async-lib",
                "technologies": ["Python", "asyncio"],
                "achievements": ["1000+ GitHub stars"]
            }
        ],
        "certifications": [
            "AWS Certified Solutions Architect",
            "Kubernetes Administrator (CKA)"
        ],
        "languages": [
            {"language": "English", "level": "Native"},
            {"language": "Spanish", "level": "Professional"}
        ]
    }
