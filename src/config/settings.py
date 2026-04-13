"""Configuration settings.

Rule applied here:
  • Same for everyone and safe to publish  →  default defined in this file only.
  • Changes per environment or is a secret →  empty/None default here; set in .env.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings

_JWT_SECRET_MIN_LENGTH = 32


class Settings(BaseSettings):
    """Application settings."""

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    app_name: str = "LinkedIn Job Application Agent"
    debug: bool = False       # env-specific — override in .env
    log_level: str = "INFO"   # env-specific — override in .env

    # -------------------------------------------------------------------------
    # LinkedIn Credentials  (secrets — set in .env)
    # -------------------------------------------------------------------------
    linkedin_email: str = ""
    linkedin_password: str = ""
    linkedin_api_key: str | None = None

    # -------------------------------------------------------------------------
    # LLM Provider Selection  (env-specific — override in .env)
    # -------------------------------------------------------------------------
    primary_llm_provider: str = "openai"      # openai | deepseek | grok | anthropic

    # -------------------------------------------------------------------------
    # LLM API Keys  (secrets — set in .env)
    # -------------------------------------------------------------------------
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    grok_api_key: str | None = None
    anthropic_api_key: str | None = None

    # -------------------------------------------------------------------------
    # LLM Model Names  (env-specific — defaults safe for most deployments)
    # -------------------------------------------------------------------------
    # OpenAI: gpt-4o or newer required for strict JSON Schema support
    openai_model: str = "gpt-4o"
    # DeepSeek: json_object mode only (no strict schemas)
    deepseek_model: str = "deepseek-chat"
    # Grok: grok-2-1212+ required for structured outputs
    grok_model: str = "grok-2-1212"
    # Anthropic: claude-sonnet-4.5+ required for structured outputs (beta)
    anthropic_model: str = "claude-sonnet-4.5"

    # -------------------------------------------------------------------------
    # Paths  (same for everyone — defaults defined here, no need to set in .env)
    # -------------------------------------------------------------------------
    data_dir: str = "./data"
    cv_dir: str = "./data/cv"
    jobs_dir: str = "./data/jobs"
    generated_cvs_dir: str = "./data/generated_cvs"
    master_cv_path: str = "./data/cv/master_cv.json"
    prompts_dir: str = "./prompts/cv_composer"

    # -------------------------------------------------------------------------
    # Repository  (env-specific — override in .env)
    # -------------------------------------------------------------------------
    repo_type: str = "memory"           # "memory" (dev) | "sqlite" (production)
    db_path: str = "./data/jobs.db"     # SQLite path; same for everyone by default

    # -------------------------------------------------------------------------
    # PDF / CV Template  (same for everyone — change via CV_TEMPLATE_NAME in .env)
    # -------------------------------------------------------------------------
    cv_template_dir: str = "src/templates/cv"
    cv_template_name: str = "compact"   # modern | compact | classic | minimal | profile-card

    # -------------------------------------------------------------------------
    # LinkedIn Search — global fallback (env-specific — set in .env)
    # -------------------------------------------------------------------------
    linkedin_search_keywords: str = ""
    linkedin_search_location: str = ""
    linkedin_search_remote_filter: str | None = None    # "remote" | "on-site" | "hybrid"
    linkedin_search_date_posted: str | None = None      # "24h" | "week" | "month"
    linkedin_search_experience_level: list[str] | None = None
    linkedin_search_job_type: list[str] | None = None

    # Same for everyone — tune in .env only if you need different pacing
    linkedin_search_easy_apply_only: bool = False
    linkedin_search_max_jobs: int = 50
    linkedin_session_cookie_path: str = "./data/linkedin_cookies.json"
    linkedin_min_delay: float = 3.0
    linkedin_max_delay: float = 8.0
    linkedin_page_delay_min: float = 2.0
    linkedin_page_delay_max: float = 5.0

    # -------------------------------------------------------------------------
    # LinkedIn Scheduler  (env-specific — override in .env)
    # -------------------------------------------------------------------------
    linkedin_search_schedule_enabled: bool = False
    linkedin_search_interval_hours: int = 1     # same for everyone; change via .env if needed

    # -------------------------------------------------------------------------
    # Job Fixture Record & Replay  (env-specific — for dev/demo use)
    # -------------------------------------------------------------------------
    seed_jobs_from_file: bool = False
    scraped_jobs_path: str = "./data/jobs/scraped_jobs.json"
    seed_jobs_limit: int = 0    # 0 = no limit

    # -------------------------------------------------------------------------
    # Workflow  (same for everyone — defaults defined here)
    # -------------------------------------------------------------------------
    job_fetch_interval_hours: int = 1
    max_concurrent_applications: int = 3
    browser_headless: bool = True   # env-specific: set false in .env for visual debugging

    # -------------------------------------------------------------------------
    # CV Composer  (same for everyone — defaults defined here)
    # -------------------------------------------------------------------------
    cv_composer_temperature_summary: float = 0.5        # creative for professional summary
    cv_composer_temperature_job_analysis: float = 0.3   # precise for job analysis
    cv_composer_temperature_sections: float = 0.4       # balanced for CV sections
    cv_composer_max_retries: int = 3
    cv_composer_enable_hallucination_checks: bool = True
    cv_composer_hallucination_policy: str = "strict"    # "strict" | "warn" | "disabled"
    cv_composer_model_override: str | None = None       # env-specific override

    # -------------------------------------------------------------------------
    # CV Length Limits  (same for everyone — targets a 2-page output)
    # -------------------------------------------------------------------------
    cv_max_experiences: int = 4
    cv_max_achievements_per_experience: int = 4
    cv_max_skills: int = 15
    cv_max_projects: int = 2
    cv_max_certifications: int = 4
    cv_target_pages: int = 2

    # -------------------------------------------------------------------------
    # Authentication  (secrets + env-specific — set in .env)
    # -------------------------------------------------------------------------
    resend_api_key: str = ""
    resend_from: str = "LinkedIn Agent <noreply@resend.dev>"
    app_url: str = "http://localhost:5173"      # base URL for magic link callback
    magic_link_ttl_minutes: int = 15            # same for everyone
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_days: int = 30                   # same for everyone

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_be_strong(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "JWT_SECRET must not be empty. Set a strong unique value in .env."
            )
        if v == "change-me-in-production":
            # Allow the placeholder at import time so tests that don't exercise
            # auth can still import Settings. AuthService re-checks at runtime
            # before signing any token.
            return v
        if len(v) < _JWT_SECRET_MIN_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {_JWT_SECRET_MIN_LENGTH} characters. "
                "Use a randomly generated value (e.g. `openssl rand -hex 32`)."
            )
        return v

    # -------------------------------------------------------------------------
    # API Server  (env-specific — override in .env)
    # -------------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Return the application settings singleton."""
    return Settings()
