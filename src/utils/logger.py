"""Logging configuration"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Project root directory (where logs/ will be created)
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

# Timestamp for current run (set once at module import)
_RUN_TIMESTAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def setup_logger(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Setup and configure logger

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for file logging

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if logger already configured
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper()))

    # Formatter with timestamp
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, level.upper()))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def setup_api_logger(level: str = "INFO") -> logging.Logger:
    """
    Setup logger for API with file output to logs/api_<timestamp>.log

    Creates a new log file for each API run with timestamp in filename.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Configured logger instance for API
    """
    log_file = DEFAULT_LOG_DIR / f"api_{_RUN_TIMESTAMP}.log"
    return setup_logger("api", level=level, log_file=str(log_file))
