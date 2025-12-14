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

    # OpenAI
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4"

    # DeepSeek
    deepseek_api_key: Optional[str] = None
    deepseek_model: str = "deepseek-chat"

    # Grok
    grok_api_key: Optional[str] = None
    grok_model: str = "grok-beta"

    # Anthropic
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Paths
    data_dir: str = "./data"
    cv_dir: str = "./data/cv"
    jobs_dir: str = "./data/jobs"
    generated_cvs_dir: str = "./data/generated_cvs"
    master_cv_path: str = "./data/cv/master_cv.json"

    # Workflow
    job_fetch_interval_hours: int = 1
    max_concurrent_applications: int = 3
    browser_headless: bool = True

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
