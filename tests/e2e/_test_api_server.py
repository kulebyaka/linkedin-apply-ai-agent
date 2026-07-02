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

    _test_settings = Settings(
        _env_file=None,
        cors_origins=[],  # Empty — we'll override CORS with regex below
        cv_composer_hallucination_policy="disabled",
        jwt_secret="test-secret-for-e2e-tests-extended",
    )
    _settings_mod.get_settings = lambda: _test_settings

    # Apply patches BEFORE importing the app (which triggers module-level init)
    # Both preparation and retry workflows now use create_llm_client from _shared
    p1 = patch(
        "src.agents._shared.create_llm_client",
        side_effect=lambda *a, **kw: _make_mock_llm_client(),
    )
    p1.start()

    # Override auth dependency to return a test user for e2e tests.
    # Granting admin role so admin UI/API tests can authenticate; non-admin
    # tests are unaffected because they don't gate on role.
    from src.api.main import app, get_current_user
    from src.models.user import User, UserRole

    _test_user = User(
        id="e2e-test-user",
        email="e2e@test.com",
        display_name="E2E Test",
        role=UserRole.ADMIN,
    )
    app.dependency_overrides[get_current_user] = lambda: _test_user

    # Test-only endpoint to seed an extra user into the repo so admin UI tests
    # can exercise the /admin/users flow (toggle role on a non-self user).
    # We register the route then move it to the front of app.routes so it wins
    # over a possible StaticFiles mount at "/" from a prior `npm run build`.
    from fastapi import Request as _FastApiRequest

    @app.post("/__test__/seed-user")
    async def _seed_user(req: _FastApiRequest) -> dict:
        body = await req.json()
        ctx = req.app.state.ctx
        existing = await ctx.user_repository.get_by_email(body["email"])
        if existing is not None:
            return {
                "id": existing.id,
                "email": existing.email,
                "role": existing.role.value,
            }
        u = await ctx.user_repository.create_user(body["email"])
        return {"id": u.id, "email": u.email, "role": u.role.value}

    # Test-only endpoint to materialize the static override user into the repo
    # with its fixed id, so routes that persist by `user.id` (e.g. PUT
    # /api/users/me) succeed instead of raising "user not found".
    @app.post("/__test__/ensure-test-user")
    async def _ensure_test_user(req: _FastApiRequest) -> dict:
        from datetime import datetime, timezone

        from src.services.db.tables import UserTable

        existing = (
            await UserTable.select().where(UserTable.id == _test_user.id).first().run()
        )
        if existing:
            return {"id": _test_user.id, "created": False}
        now = datetime.now(tz=timezone.utc)
        await UserTable.insert(
            UserTable(
                id=_test_user.id,
                email=_test_user.email,
                display_name=_test_user.display_name,
                role=_test_user.role.value,
                master_cv_json=None,
                search_preferences=None,
                created_at=now,
                updated_at=now,
            )
        ).run()
        return {"id": _test_user.id, "created": True}

    # Pop the freshly added test-only routes and re-insert them at the front of
    # app.routes so they precede any StaticFiles mount registered at import time.
    test_routes = [r for r in app.router.routes if getattr(r, "path", "").startswith("/__test__")]
    for r in test_routes:
        app.router.routes.remove(r)
    for r in reversed(test_routes):
        app.router.routes.insert(0, r)

    # Replace the CORS middleware to allow any localhost/127.0.0.1 origin.
    # The default cors_origins setting uses specific ports, but e2e tests use
    # random ports. Using allow_origin_regex with allow_credentials=True
    # makes the browser accept cross-origin responses with credentials.
    from starlette.middleware.cors import CORSMiddleware

    # Remove existing CORS middleware and re-add with regex
    app.user_middleware = [
        m for m in app.user_middleware
        if m.cls is not CORSMiddleware
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Force middleware stack rebuild
    app.middleware_stack = None
    app.build_middleware_stack()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
