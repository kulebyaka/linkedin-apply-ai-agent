"""Test API server entrypoint with mocked LLM.

This script is started as a subprocess by conftest.py.  It patches
_init_llm_client in both workflow modules so that no real LLM API calls
are made, then runs uvicorn on the port passed as the first CLI argument.
"""

import itertools
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Canned LLM responses (must match what tests expect)
# ---------------------------------------------------------------------------

CANNED_JOB_SUMMARY = {
    "technical_skills": ["Python", "Django", "AWS", "Docker", "PostgreSQL"],
    "soft_skills": ["Problem-solving", "Communication"],
    "education_reqs": ["Bachelor's degree in Computer Science"],
    "experience_reqs": {"years": 5, "level": "senior"},
    "responsibilities": [
        "Design and implement microservices architecture",
        "Lead development team",
    ],
    "nice_to_have": ["Kubernetes"],
}

CANNED_CV_SECTIONS = {
    "summary": (
        "Senior Software Engineer with 8+ years of experience specializing "
        "in Python, Django, and AWS. Proven track record of building scalable "
        "microservices architectures."
    ),
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
                "Reduced infrastructure costs by 40%",
                "Led team of 5 engineers in agile environment",
            ],
            "technologies": ["Python", "Django", "AWS", "Docker", "Kubernetes"],
        },
    ],
    "education": [
        {
            "institution": "Stanford University",
            "degree": "Bachelor of Science",
            "field_of_study": "Computer Science",
            "start_date": "2011-09-01",
            "end_date": "2015-06-15",
            "is_current": False,
            "gpa": "3.8",
            "achievements": ["Dean's List all semesters"],
        },
    ],
    "skills": [
        {"name": "Python", "category": "Programming Languages", "proficiency": "expert"},
        {"name": "Django", "category": "Frameworks", "proficiency": "expert"},
        {"name": "AWS", "category": "Cloud & DevOps", "proficiency": "advanced"},
    ],
    "projects": [
        {
            "name": "OpenSource ML Library",
            "description": "Machine learning library for time-series prediction",
            "technologies": ["Python", "TensorFlow"],
            "achievements": ["1000+ GitHub stars"],
        },
    ],
    "certifications": [
        {
            "name": "AWS Certified Solutions Architect",
            "issuer": "Amazon Web Services",
            "date": "2022-01-01",
        },
    ],
}


def _make_mock_llm_client():
    """Return a mock BaseLLMClient whose generate_json returns canned data."""
    mock_client = MagicMock()
    mock_client.generate_json.side_effect = itertools.cycle(
        [CANNED_JOB_SUMMARY, CANNED_CV_SECTIONS]
    )
    return mock_client


def main():
    port = int(sys.argv[1])

    # Override get_settings so it doesn't choke on VITE_* vars from .env.
    # Must happen before any application module is imported.
    import os

    from dotenv import dotenv_values

    # Load .env but filter out non-Settings keys (e.g. VITE_*)
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        for key, val in dotenv_values(env_path).items():
            if not key.startswith("VITE_") and val is not None:
                os.environ.setdefault(key, val)

    # Force in-memory repository for tests regardless of .env settings
    os.environ["REPO_TYPE"] = "memory"

    from src.config.settings import Settings, get_settings

    get_settings.cache_clear()

    import src.config.settings as _settings_mod

    _test_settings = Settings(_env_file=None, cors_origins=["*"])
    _settings_mod.get_settings = lambda: _test_settings

    # Apply patches BEFORE importing the app (which triggers module-level init)
    # Both preparation and retry workflows now use create_llm_client from _shared
    p1 = patch(
        "src.agents._shared.create_llm_client",
        side_effect=lambda *a, **kw: _make_mock_llm_client(),
    )
    p1.start()

    import uvicorn

    uvicorn.run("src.api.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
