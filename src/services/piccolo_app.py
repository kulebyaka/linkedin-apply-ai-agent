"""Piccolo app configuration for job repository."""

import os

from piccolo.conf.apps import AppConfig, table_finder

CURRENT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))

APP_CONFIG = AppConfig(
    app_name="job_repository",
    table_classes=table_finder(
        modules=["src.services.tables"],
        exclude_imported=True,
    ),
    migrations_folder_path=os.path.join(CURRENT_DIRECTORY, "piccolo_migrations"),
)
