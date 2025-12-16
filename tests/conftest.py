"""Shared test fixtures and configuration"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock


# Fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir():
    """Return fixtures directory path"""
    return FIXTURES_DIR


@pytest.fixture
def sample_master_cv():
    """Load sample master CV"""
    with open(FIXTURES_DIR / "master_cv.json") as f:
        return json.load(f)


@pytest.fixture
def sample_job_posting():
    """Load sample job posting"""
    with open(FIXTURES_DIR / "job_posting.json") as f:
        return json.load(f)


@pytest.fixture
def sample_job_summary():
    """Sample job summary dict"""
    return {
        "technical_skills": ["Python", "Django", "AWS", "Docker", "PostgreSQL"],
        "soft_skills": ["Problem-solving", "Communication", "Leadership"],
        "education_reqs": ["Bachelor's degree in Computer Science"],
        "experience_reqs": {
            "years": 5,
            "level": "senior"
        },
        "responsibilities": [
            "Design and implement microservices architecture",
            "Lead development team",
            "Optimize system performance"
        ],
        "nice_to_have": ["Kubernetes", "Flask", "Machine Learning"]
    }


@pytest.fixture
def mock_llm_response_job_summary():
    """Mock LLM response for job summarization"""
    return {
        "technical_skills": ["Python", "Django", "AWS"],
        "soft_skills": ["Communication"],
        "education_reqs": ["Bachelor's"],
        "experience_reqs": {"years": 5, "level": "senior"},
        "responsibilities": ["Build APIs"],
        "nice_to_have": ["Docker"]
    }


@pytest.fixture
def mock_llm_response_summary():
    """Mock LLM response for professional summary"""
    return {
        "summary": "Senior Software Engineer with 8+ years of experience specializing in Python, Django, and AWS. Proven track record of building scalable microservices architectures."
    }


@pytest.fixture
def mock_llm_response_experiences():
    """Mock LLM response for experiences"""
    return [
        {
            "company": "Tech Corp",
            "position": "Senior Software Engineer",
            "start_date": "2020-01-15",
            "end_date": None,
            "is_current": True,
            "location": "San Francisco, CA",
            "description": "Lead development of cloud-based microservices platform",
            "achievements": [
                "Architected microservices platform with Python and Django serving 1M+ users",
                "Deployed on AWS with Docker and Kubernetes",
                "Reduced infrastructure costs by 40%"
            ],
            "technologies": ["Python", "Django", "AWS", "Docker", "Kubernetes", "PostgreSQL"]
        }
    ]


# LLM Client Fixtures (Mock vs Real based on test type)
@pytest.fixture
def llm_client(request):
    """
    Returns appropriate LLM client based on test type
    - Unit tests: MockLLMClient (fast, no API calls)
    - Eval tests: Real LLM client (slow, API calls)

    The fixture checks for the 'eval' marker on the test to determine which to use.
    """
    # Check if test has 'eval' marker
    if 'eval' in request.keywords:
        # Return real LLM client for eval tests
        from tests.helpers.llm_clients import create_real_llm_client
        return create_real_llm_client()
    else:
        # Return mock for unit tests
        from tests.unit.test_cv_composer import MockLLMClient
        return MockLLMClient()


@pytest.fixture
def cv_composer(llm_client):
    """Create CV composer with appropriate LLM client"""
    from src.services.cv_composer import CVComposer
    return CVComposer(llm_client=llm_client)


# Pytest configuration
def pytest_configure(config):
    """Configure pytest"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
