"""Tests for CV Composer service"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from src.services.cv_composer import CVComposer
from src.models.cv import CV, JobSummary
from src.llm.provider import BaseLLMClient


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


class TestSectionComposers:
    """Test individual section composition methods"""

    @pytest.fixture
    def job_summary(self):
        """Sample job summary"""
        return {
            "technical_skills": ["Python", "Django", "AWS"],
            "soft_skills": ["Communication", "Leadership"],
            "education_reqs": ["Bachelor's"],
            "experience_reqs": {"years": 5, "level": "senior"},
            "responsibilities": ["Build APIs", "Design systems"],
            "nice_to_have": ["Docker"]
        }

    def test_compose_summary(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test professional summary composition"""
        mock_response = {
            "summary": "Senior Software Engineer with 8+ years specializing in Python and Django. Proven track record in building scalable cloud-based solutions on AWS."
        }
        mock_llm_client.set_response("professional summary", mock_response)

        result = cv_composer._compose_summary(master_cv, job_summary)

        assert isinstance(result, str)
        assert "Python" in result or "Django" in result or len(result) > 0

    def test_compose_experiences(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test experience section composition"""
        mock_response = [
            {
                "company": "Tech Corp",
                "position": "Senior Software Engineer",
                "start_date": "2020-01-15",
                "end_date": None,
                "is_current": True,
                "location": "San Francisco, CA",
                "description": "Lead development of cloud-based microservices platform",
                "achievements": [
                    "Architected microservices platform with Python and Django",
                    "Deployed on AWS with Docker containers"
                ],
                "technologies": ["Python", "Django", "AWS", "Docker"]
            }
        ]
        mock_llm_client.set_response("experience", mock_response)

        result = cv_composer._compose_experiences(master_cv, job_summary)

        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["company"] == "Tech Corp"

    def test_compose_experiences_empty(self, cv_composer, mock_llm_client, job_summary):
        """Test experience composition with no experiences"""
        empty_cv = {"experiences": []}

        result = cv_composer._compose_experiences(empty_cv, job_summary)

        assert result == []

    def test_compose_education(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test education section composition"""
        mock_response = [
            {
                "institution": "Stanford University",
                "degree": "Bachelor of Science",
                "field_of_study": "Computer Science",
                "start_date": "2011-09-01",
                "end_date": "2015-06-15",
                "is_current": False,
                "location": None,
                "grade": "3.8",
                "achievements": ["Dean's List", "CS Club President"]
            }
        ]
        mock_llm_client.set_response("education", mock_response)

        result = cv_composer._compose_education(master_cv, job_summary)

        assert isinstance(result, list)
        assert len(result) > 0

    def test_compose_skills(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test skills section composition"""
        mock_response = [
            {
                "category": "Programming Languages",
                "skills": [
                    {"name": "Python", "proficiency": "expert"},
                    {"name": "JavaScript", "proficiency": "advanced"}
                ]
            },
            {
                "category": "Frameworks",
                "skills": [
                    {"name": "Django", "proficiency": "expert"}
                ]
            }
        ]
        mock_llm_client.set_response("skills", mock_response)

        result = cv_composer._compose_skills(master_cv, job_summary)

        assert isinstance(result, list)
        assert len(result) > 0
        assert "category" in result[0]
        assert "skills" in result[0]

    def test_compose_projects(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test projects section composition"""
        mock_response = [
            {
                "name": "OpenSource ML Library",
                "description": "Machine learning library for time-series prediction using Python",
                "role": None,
                "start_date": None,
                "end_date": None,
                "technologies": ["Python", "TensorFlow"],
                "achievements": ["1000+ stars"],
                "url": "https://github.com/johndoe/ml-lib"
            }
        ]
        mock_llm_client.set_response("projects", mock_response)

        result = cv_composer._compose_projects(master_cv, job_summary)

        assert isinstance(result, list)
        assert len(result) > 0

    def test_compose_certifications(self, cv_composer, mock_llm_client, master_cv, job_summary):
        """Test certifications composition"""
        mock_response = [
            "AWS Certified Solutions Architect - Associate (2022)",
            "Certified Kubernetes Administrator (2021)"
        ]
        mock_llm_client.set_response("certifications", mock_response)

        result = cv_composer._compose_certifications(master_cv, job_summary)

        assert isinstance(result, list)


class TestValidation:
    """Test CV validation and hallucination detection"""

    def test_validate_output_success(self, cv_composer, master_cv):
        """Test successful validation"""
        # Create valid tailored CV (same as master for this test)
        tailored_cv = master_cv.copy()

        result = cv_composer._validate_output(tailored_cv, master_cv)

        assert isinstance(result, dict)
        assert "contact" in result
        assert "experiences" in result

    def test_validate_output_schema_validation(self, cv_composer, master_cv):
        """Test that Pydantic schema validation works"""
        tailored_cv = master_cv.copy()

        # Should not raise
        result = cv_composer._validate_output(tailored_cv, master_cv)

        # Validate it matches CV model
        cv = CV(**result)
        assert cv.contact.full_name == "John Doe"

    def test_validate_output_invalid_schema(self, cv_composer, master_cv):
        """Test validation fails with invalid schema"""
        # Create invalid CV (missing required fields)
        invalid_cv = {"contact": {}}  # Missing required contact fields

        with pytest.raises(ValueError) as exc_info:
            cv_composer._validate_output(invalid_cv, master_cv)

        assert "schema" in str(exc_info.value).lower()

    def test_validate_detects_hallucinated_companies(self, cv_composer, master_cv):
        """Test that hallucinated companies are detected"""
        tailored_cv = master_cv.copy()

        # Add fake company
        tailored_cv["experiences"].append({
            "company": "Fake Corp",
            "position": "Engineer",
            "start_date": "2020-01-01",
            "end_date": None,
            "is_current": True,
            "location": "Nowhere",
            "description": "Fake job",
            "achievements": [],
            "technologies": []
        })

        # Should log warning but still validate
        # (current implementation logs warning, doesn't raise)
        with patch('src.services.cv_composer.logger') as mock_logger:
            result = cv_composer._validate_output(tailored_cv, master_cv)
            mock_logger.warning.assert_called()

    def test_validate_detects_hallucinated_institutions(self, cv_composer, master_cv):
        """Test that hallucinated educational institutions are detected"""
        tailored_cv = master_cv.copy()

        # Add fake institution
        tailored_cv["education"].append({
            "institution": "Fake University",
            "degree": "PhD",
            "field_of_study": "Computer Science",
            "start_date": "2015-09-01",
            "end_date": "2019-06-01",
            "gpa": None,
            "achievements": []
        })

        with patch('src.services.cv_composer.logger') as mock_logger:
            result = cv_composer._validate_output(tailored_cv, master_cv)
            mock_logger.warning.assert_called()


class TestFullCVComposition:
    """Integration tests for full CV composition"""

    def test_compose_cv_full_workflow(self, cv_composer, mock_llm_client, master_cv, job_posting):
        """Test complete CV composition workflow"""
        # Set up all mock responses
        mock_llm_client.set_response("job description", {
            "technical_skills": ["Python", "Django", "AWS"],
            "soft_skills": ["Communication"],
            "education_reqs": ["Bachelor's"],
            "experience_reqs": {"years": 5, "level": "senior"},
            "responsibilities": ["Build systems"],
            "nice_to_have": []
        })

        mock_llm_client.set_response("professional summary", {
            "summary": "Experienced Python engineer"
        })

        mock_llm_client.set_response("experience", [{
            "company": "Tech Corp",
            "position": "Senior Software Engineer",
            "start_date": "2020-01-15",
            "end_date": None,
            "is_current": True,
            "location": "San Francisco, CA",
            "description": "Lead development",
            "achievements": ["Built platform"],
            "technologies": ["Python", "Django"]
        }])

        mock_llm_client.set_response("education", [{
            "institution": "Stanford University",
            "degree": "Bachelor of Science",
            "field_of_study": "Computer Science",
            "start_date": "2011-09-01",
            "end_date": "2015-06-15",
            "is_current": False,
            "location": None,
            "grade": "3.8",
            "achievements": []
        }])

        mock_llm_client.set_response("skills", [{
            "category": "Programming Languages",
            "skills": [{"name": "Python", "proficiency": "expert"}]
        }])

        mock_llm_client.set_response("projects", [{
            "name": "ML Library",
            "description": "Machine learning",
            "role": None,
            "start_date": None,
            "end_date": None,
            "technologies": ["Python"],
            "achievements": [],
            "url": None
        }])

        mock_llm_client.set_response("certifications", [
            "AWS Certified Solutions Architect - Associate (2022)"
        ])

        # Execute
        result = cv_composer.compose_cv(master_cv, job_posting)

        # Validate
        assert isinstance(result, dict)
        assert "contact" in result
        assert "summary" in result
        assert "experiences" in result
        assert "education" in result
        assert "skills" in result
        assert "projects" in result
        assert "certifications" in result

        # Contact should be unchanged
        assert result["contact"] == master_cv["contact"]

        # Languages should be unchanged
        assert result["languages"] == master_cv["languages"]

    def test_compose_cv_preserves_contact_info(self, cv_composer, mock_llm_client, master_cv, job_posting):
        """Test that contact information is preserved unchanged"""
        # Set minimal mock responses
        mock_llm_client.set_response("job", {
            "technical_skills": [],
            "soft_skills": [],
            "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [],
            "nice_to_have": []
        })
        mock_llm_client.set_response("summary", {"summary": "Test"})
        mock_llm_client.set_response("experience", [])
        mock_llm_client.set_response("education", [])
        mock_llm_client.set_response("skills", [])
        mock_llm_client.set_response("projects", [])
        mock_llm_client.set_response("certifications", [])

        result = cv_composer.compose_cv(master_cv, job_posting)

        # Contact info should be exactly the same
        assert result["contact"] == master_cv["contact"]
        assert result["contact"]["full_name"] == "John Doe"
        assert result["contact"]["email"] == "john.doe@example.com"

    def test_compose_cv_calls_all_sections(self, cv_composer, mock_llm_client, master_cv, job_posting):
        """Test that all composition methods are called"""
        # Set up basic responses
        mock_llm_client.set_response("job", {
            "technical_skills": [], "soft_skills": [], "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [], "nice_to_have": []
        })
        mock_llm_client.set_response("summary", {"summary": "Test"})
        mock_llm_client.set_response("experience", [])
        mock_llm_client.set_response("education", [])
        mock_llm_client.set_response("skills", [])
        mock_llm_client.set_response("projects", [])
        mock_llm_client.set_response("certifications", [])

        with patch.object(cv_composer, '_compose_summary', return_value="Test") as mock_summary, \
             patch.object(cv_composer, '_compose_experiences', return_value=[]) as mock_exp, \
             patch.object(cv_composer, '_compose_education', return_value=[]) as mock_edu, \
             patch.object(cv_composer, '_compose_skills', return_value=[]) as mock_skills, \
             patch.object(cv_composer, '_compose_projects', return_value=[]) as mock_proj, \
             patch.object(cv_composer, '_compose_certifications', return_value=[]) as mock_cert:

            cv_composer.compose_cv(master_cv, job_posting)

            # Verify all methods were called
            mock_summary.assert_called_once()
            mock_exp.assert_called_once()
            mock_edu.assert_called_once()
            mock_skills.assert_called_once()
            mock_proj.assert_called_once()
            mock_cert.assert_called_once()


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_cv(self, cv_composer, mock_llm_client, job_posting):
        """Test with minimal/empty CV"""
        empty_cv = {
            "contact": {
                "full_name": "Test User",
                "email": "test@example.com",
                "phone": None,
                "location": None,
                "linkedin_url": None,
                "github_url": None,
                "portfolio_url": None
            },
            "summary": "",
            "experiences": [],
            "education": [],
            "skills": [],
            "projects": [],
            "certifications": [],
            "languages": []
        }

        # Set up responses
        mock_llm_client.set_response("job", {
            "technical_skills": [], "soft_skills": [], "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [], "nice_to_have": []
        })
        mock_llm_client.set_response("summary", {"summary": "Entry-level professional"})

        result = cv_composer.compose_cv(empty_cv, job_posting)

        assert result["experiences"] == []
        assert result["education"] == []

    def test_minimal_job_posting(self, cv_composer, mock_llm_client, master_cv):
        """Test with minimal job posting"""
        minimal_job = {
            "title": "Developer",
            "company": "Company",
            "description": "",
            "requirements": ""
        }

        mock_llm_client.set_response("job", {
            "technical_skills": [], "soft_skills": [], "education_reqs": [],
            "experience_reqs": {"years": None, "level": None},
            "responsibilities": [], "nice_to_have": []
        })
        mock_llm_client.set_response("summary", {"summary": "Test"})
        mock_llm_client.set_response("experience", master_cv["experiences"])
        mock_llm_client.set_response("education", master_cv["education"])
        mock_llm_client.set_response("skills", [])
        mock_llm_client.set_response("projects", [])
        mock_llm_client.set_response("certifications", [])

        result = cv_composer.compose_cv(master_cv, minimal_job)

        assert isinstance(result, dict)
