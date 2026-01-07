"""Piccolo ORM configuration for SQLite persistence."""

from piccolo.conf.apps import AppRegistry
from piccolo.engine.sqlite import SQLiteEngine

from src.config.settings import get_settings

settings = get_settings()

# Database engine - path from settings
DB = SQLiteEngine(path=settings.db_path)

# Register Piccolo apps
APP_REGISTRY = AppRegistry(
    apps=["src.services.piccolo_app"]
)
