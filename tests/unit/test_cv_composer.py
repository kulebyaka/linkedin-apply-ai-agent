"""Tests for CV Composer service"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.llm.provider import BaseLLMClient
from src.models.cv import CVLLMOutput, JobSummary
from src.services.cv_composer import CVComposer, CVCompositionError

# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class MockLLMClient(BaseLLMClient):
    """Mock LLM client for testing"""

    def __init__(self):
        super().__init__(api_key="test-key", model="test-model")
        self.responses = {}
        self.call_count = 0

    def set_response(self, prompt_keyword: str, response: dict):
        """Set a mock response for prompts containing keyword"""
        self.responses[prompt_keyword] = response

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Mock generate method"""
        return "Mock response"

    def generate_json(
        self,
        prompt: str,
        schema: dict = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> dict:
        """Mock generate_json method"""
        self.call_count += 1

        # Return appropriate response based on prompt content
        for keyword, response in self.responses.items():
            if keyword.lower() in prompt.lower():
                return response

        # Default response
        return {}


@pytest.fixture
def master_cv():
    """Load master CV fixture"""
    with open(FIXTURES_DIR / "master_cv.json") as f:
        return json.load(f)


@pytest.fixture
def job_posting():
    """Load job posting fixture"""
    with open(FIXTURES_DIR / "job_posting.json") as f:
        return json.load(f)


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client"""
    return MockLLMClient()


@pytest.fixture
def cv_composer(mock_llm_client):
    """Create CV composer with mock LLM client"""
    return CVComposer(llm_client=mock_llm_client)


class TestJobSummarization:
    """Test job description summarization"""

    def test_summarize_job_basic(self, cv_composer, mock_llm_client, job_posting):
        """Test basic job summarization"""
        # Set up mock response
        mock_response = {
            "technical_skills": ["Python", "Django", "AWS", "Docker", "PostgreSQL"],
            "soft_skills": ["Problem-solving", "Communication"],
            "education_reqs": ["Bachelor's degree"],
            "experience_reqs": {"years": 5, "level": "senior"},
            "responsibilities": ["Design microservices", "Implement scalable solutions"],
            "nice_to_have": ["Kubernetes", "Flask"]
        }
        mock_llm_client.set_response("job description", mock_response)

        # Test
        result = cv_composer._summarize_job(job_posting)

        assert "technical_skills" in result
        assert "Python" in result["technical_skills"]
        assert "Django" in result["technical_skills"]
        assert result["experience_reqs"]["years"] == 5
        assert result["experience_reqs"]["level"] == "senior"

    def test_summarize_job_validates_with_pydantic(self, cv_composer, mock_llm_client, job_posting):
        """Test that job summary is validated with Pydantic"""
        # Set up valid mock response
        mock_response = {
            "technical_skills": ["Python"],
            "soft_skills": ["Communication"],
            "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": ["Develop software"],
            "nice_to_have": []
        }
        mock_llm_client.set_response("job description", mock_response)

        result = cv_composer._summarize_job(job_posting)

        # Should return valid JobSummary dict
        assert isinstance(result, dict)
        job_summary = JobSummary(**result)
        assert job_summary.technical_skills == ["Python"]

    def test_summarize_job_empty_description(self, cv_composer, mock_llm_client):
        """Test summarization with empty job description"""
        empty_job = {
            "title": "Developer",
            "company": "Company",
            "description": "",
            "requirements": ""
        }

        mock_response = {
            "technical_skills": [],
            "soft_skills": [],
            "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [],
            "nice_to_have": []
        }
        mock_llm_client.set_response("job description", mock_response)

        result = cv_composer._summarize_job(empty_job)

        assert result["technical_skills"] == []
        assert result["soft_skills"] == []


class TestComposeCVIntegration:
    """Test compose_cv() end-to-end flow"""

    def test_compose_cv_end_to_end(self, cv_composer, mock_llm_client, master_cv, job_posting):
        """Test that compose_cv orchestrates summarize -> compose -> validate correctly"""
        # Master CV needs 'contact' key for pass-through validation
        master_cv_with_contact = {
            **master_cv,
            "contact": master_cv.get("contact_info", {
                "full_name": "John Doe",
                "email": "john@example.com",
            }),
        }

        # Mock response for job summary (triggered by "job description" in prompt)
        mock_llm_client.set_response("job description", {
            "technical_skills": ["Python", "Django", "AWS", "Docker"],
            "soft_skills": ["Communication"],
            "education_reqs": ["Bachelor's degree"],
            "experience_reqs": {"years": 5, "level": "senior"},
            "responsibilities": ["Design microservices"],
            "nice_to_have": ["Kubernetes"],
        })

        # Mock response for full CV generation (triggered by "master cv" in prompt)
        mock_llm_client.set_response("master cv", {
            "summary": "Senior Software Engineer with 8+ years of experience",
            "experiences": [
                {
                    "company": "Tech Corp",
                    "position": "Senior Software Engineer",
                    "start_date": "2020-01-15",
                    "end_date": None,
                    "is_current": True,
                    "location": "San Francisco, CA",
                    "description": "Lead development of cloud-based microservices platform",
                    "achievements": [
                        "Architected microservices platform serving 1M+ users",
                        "Reduced deployment time by 60%",
                    ],
                    "technologies": ["Python", "Django", "AWS", "Docker"],
                }
            ],
            "education": [
                {
                    "institution": "Stanford University",
                    "degree": "Bachelor of Science",
                    "field_of_study": "Computer Science",
                    "start_date": "2011-09-01",
                    "end_date": "2015-06-15",
                }
            ],
            "skills": [
                {"name": "Python", "category": "Programming"},
                {"name": "AWS", "category": "Cloud"},
            ],
            "projects": [],
            "certifications": [],
        })

        result = cv_composer.compose_cv(master_cv_with_contact, job_posting)

        assert isinstance(result, CVLLMOutput)
        assert len(result.experiences) == 1
        assert result.experiences[0].company == "Tech Corp"
        assert result.summary == "Senior Software Engineer with 8+ years of experience"
        assert result.contact is not None
        assert result.contact.full_name == "John Doe"
        # Verify both LLM calls were made (summary + full CV)
        assert mock_llm_client.call_count == 2

    def test_compose_cv_applies_length_limits(self, mock_llm_client, master_cv, job_posting):
        """Test that compose_cv enforces length limits from settings"""
        from src.services.cv_composer import CVComposerSettings

        settings = CVComposerSettings(cv_max_experiences=1, cv_max_skills=1)
        composer = CVComposer(llm_client=mock_llm_client, settings=settings)

        master_cv_with_contact = {
            **master_cv,
            "contact": master_cv.get("contact_info", {
                "full_name": "John Doe",
                "email": "john@example.com",
            }),
        }

        mock_llm_client.set_response("job description", {
            "technical_skills": ["Python"],
            "soft_skills": [],
            "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [],
            "nice_to_have": [],
        })

        mock_llm_client.set_response("master cv", {
            "summary": "Engineer",
            "experiences": [
                {
                    "company": "Tech Corp",
                    "position": "Engineer",
                    "start_date": "2020-01-01",
                    "end_date": None,
                    "is_current": True,
                    "location": "SF",
                    "description": "Dev",
                    "achievements": [],
                    "technologies": [],
                },
                {
                    "company": "Tech Corp",
                    "position": "Junior Engineer",
                    "start_date": "2018-01-01",
                    "end_date": "2019-12-31",
                    "is_current": False,
                    "location": "SF",
                    "description": "Dev",
                    "achievements": [],
                    "technologies": [],
                },
            ],
            "education": [],
            "skills": [
                {"name": "Python", "category": "Programming"},
                {"name": "Go", "category": "Programming"},
            ],
            "projects": [],
            "certifications": [],
        })

        result = composer.compose_cv(master_cv_with_contact, job_posting)

        assert len(result.experiences) == 1
        assert len(result.skills) == 1


class TestValidation:
    """Test CV validation and hallucination detection"""

    @pytest.fixture
    def valid_tailored_cv(self, master_cv):
        """Build a tailored CV dict that matches CVLLMOutput schema."""
        return {
            "summary": master_cv["summary"],
            "experiences": [
                {
                    "company": "Tech Corp",
                    "position": "Senior Software Engineer",
                    "start_date": "2020-01-15",
                    "end_date": None,
                    "is_current": True,
                    "location": "San Francisco, CA",
                    "description": "Lead development of cloud-based microservices platform",
                    "achievements": [
                        "Architected microservices platform serving 1M+ users",
                        "Reduced deployment time by 60%",
                    ],
                    "technologies": ["Python", "Django", "AWS", "Docker"],
                }
            ],
            "education": [
                {
                    "institution": "Stanford University",
                    "degree": "Bachelor of Science",
                    "field_of_study": "Computer Science",
                    "start_date": "2011-09-01",
                    "end_date": "2015-06-15",
                    "is_current": False,
                }
            ],
            "skills": [
                {"name": "Python", "category": "Programming"},
                {"name": "AWS", "category": "Cloud"},
            ],
            "projects": [],
            "certifications": [],
        }

    def test_validate_output_success(self, cv_composer, master_cv, valid_tailored_cv):
        """Test successful validation returns CVLLMOutput"""
        result = cv_composer._validate_output(valid_tailored_cv, master_cv)
        assert isinstance(result, CVLLMOutput)
        assert len(result.experiences) > 0
        assert result.summary == master_cv["summary"]

    def test_validate_output_schema_validation(self, cv_composer, master_cv, valid_tailored_cv):
        """Test that Pydantic schema validation works"""
        result = cv_composer._validate_output(valid_tailored_cv, master_cv)
        assert isinstance(result, CVLLMOutput)
        assert result.experiences[0].company == "Tech Corp"

    def test_validate_output_invalid_schema(self, cv_composer, master_cv):
        """Test validation fails with invalid schema"""
        invalid_cv = {"contact": {}}  # Missing required 'summary' field

        with pytest.raises(CVCompositionError, match="(?i)schema"):
            cv_composer._validate_output(invalid_cv, master_cv)

    def test_validate_detects_hallucinated_companies(self, cv_composer, master_cv, valid_tailored_cv):
        """Test that hallucinated companies are detected"""
        valid_tailored_cv["experiences"].append({
            "company": "Fake Corp",
            "position": "Engineer",
            "start_date": "2020-01-01",
            "end_date": None,
            "is_current": True,
            "location": "Nowhere",
            "description": "Fake job",
            "achievements": [],
            "technologies": [],
        })

        with patch("src.services.cv_composer.logger") as mock_logger:
            cv_composer._validate_output(valid_tailored_cv, master_cv)
            mock_logger.warning.assert_called()

    def test_validate_detects_hallucinated_institutions(self, cv_composer, master_cv, valid_tailored_cv):
        """Test that hallucinated educational institutions are detected"""
        valid_tailored_cv["education"].append({
            "institution": "Fake University",
            "degree": "PhD",
            "field_of_study": "Computer Science",
            "start_date": "2015-09-01",
            "end_date": "2019-06-01",
        })

        with patch("src.services.cv_composer.logger") as mock_logger:
            cv_composer._validate_output(valid_tailored_cv, master_cv)
            mock_logger.warning.assert_called()
