"""Tests for CORS_ORIGINS env parsing in Settings.

Covers both shapes accepted by the field validator:
- JSON list (legacy / .env.example default)
- Comma-separated string (real-domain deploys)
"""

import pytest

from src.config.settings import Settings


_VALID_JWT = "x" * 32


def _settings(**env) -> Settings:
    """Build Settings without reading any .env file, with the given env."""
    import os

    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ[k] = v
    try:
        return Settings(_env_file=None, jwt_secret=_VALID_JWT)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_cors_origins_defaults_to_localhost_list():
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert "http://localhost:5173" in settings.cors_origins
    assert all(isinstance(o, str) for o in settings.cors_origins)


def test_cors_origins_parses_comma_separated_string(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert settings.cors_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_origins_strips_whitespace_in_csv(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example.com , https://b.example.com ")
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert settings.cors_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_origins_ignores_empty_entries(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,,,https://b.example.com,")
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert settings.cors_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_origins_accepts_json_list(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        '["https://a.example.com","https://b.example.com"]',
    )
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert settings.cors_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_origins_single_value(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://only.example.com")
    settings = Settings(_env_file=None, jwt_secret=_VALID_JWT)
    assert settings.cors_origins == ["https://only.example.com"]
