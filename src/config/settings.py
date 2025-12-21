"""Configuration settings"""

from typing import Optional, Dict
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings"""

    # Application
    app_name: str = "LinkedIn Job Application Agent"
    debug: bool = False
    log_level: str = "INFO"

    # LinkedIn Credentials
    linkedin_email: str
    linkedin_password: str
    linkedin_api_key: Optional[str] = None

    # LLM Configuration
    primary_llm_provider: str = "openai"  # openai, deepseek, grok, anthropic
    fallback_llm_provider: Optional[str] = "deepseek"

    # OpenAI - Use gpt-4o or newer for strict JSON Schema support
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"  # gpt-4o supports structured outputs with 100% schema adherence

    # DeepSeek - Supports json_object mode only (not strict schemas)
    deepseek_api_key: Optional[str] = None
    deepseek_model: str = "deepseek-chat"

    # Grok - Use grok-2-1212 or newer for structured output support
    grok_api_key: Optional[str] = None
    grok_model: str = "grok-2-1212"  # First version with structured outputs

    # Anthropic - Use Claude Sonnet 4.5+ for structured output support
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4.5"  # Supports structured outputs (beta)

    # Paths
    data_dir: str = "./data"
    cv_dir: str = "./data/cv"
    jobs_dir: str = "./data/jobs"
    generated_cvs_dir: str = "./data/generated_cvs"
    master_cv_path: str = "./data/cv/master_cv.json"
    prompts_dir: str = "./prompts/cv_composer"

    # PDF Generation Settings
    cv_template_dir: str = "src/templates/cv"
    cv_template_name: str = "modern"  # Template theme: modern, classic, minimal

    # Workflow
    job_fetch_interval_hours: int = 1
    max_concurrent_applications: int = 3
    browser_headless: bool = True

    # CV Composer Settings
    cv_composer_temperature_summary: float = 0.5  # Creative for professional summary
    cv_composer_temperature_job_analysis: float = 0.3  # Precise for job analysis
    cv_composer_temperature_sections: float = 0.4  # Balanced for CV sections
    cv_composer_max_retries: int = 3  # Max retries for JSON generation
    cv_composer_enable_hallucination_checks: bool = True  # Validate against master CV
    cv_composer_model_override: Optional[str] = None  # Override LLM model for CV composition

    # Notifications
    webhook_url: Optional[str] = None
    notification_email: Optional[str] = None

    # API Server (for HITL UI)
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list = ["http://localhost:3000", "http://localhost:5173"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """Get application settings singleton"""
    return Settings()
