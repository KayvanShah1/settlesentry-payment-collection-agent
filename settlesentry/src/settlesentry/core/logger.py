import logging
import logging.handlers
import re
from pathlib import Path

from rich.logging import RichHandler

from settlesentry.core.settings import settings

SENSITIVE_PATTERNS = [
    # Card numbers: simple 13-19 digit matcher
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED_CARD]"),
    # CVV mentions
    (re.compile(r"(?i)(cvv\s*[:=]?\s*)\d{3,4}"), r"\1[REDACTED_CVV]"),
    # Aadhaar last 4 mentions
    (re.compile(r"(?i)(aadhaar(?:_last4| last 4)?\s*[:=]?\s*)\d{4}"), r"\1[REDACTED_AADHAAR_LAST4]"),
    # DOB mentions
    (re.compile(r"(?i)(dob|date of birth)\s*[:=]?\s*\d{4}-\d{2}-\d{2}"), r"\1=[REDACTED_DOB]"),
    # Pincode mentions
    (re.compile(r"(?i)(pincode\s*[:=]?\s*)\d{6}"), r"\1[REDACTED_PINCODE]"),
]


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        for pattern, replacement in SENSITIVE_PATTERNS:
            message = pattern.sub(replacement, message)

        record.msg = message
        record.args = ()

        return True


def _get_log_level(level_name: str) -> int:
    return getattr(logging, level_name.upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = _get_log_level(settings.logging.level)

    logger.setLevel(log_level)
    logger.propagate = False
    logger.addFilter(SensitiveDataFilter())

    if settings.logging.console_enabled:
        console_handler = RichHandler(rich_tracebacks=True)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter("%(name)s - %(message)s"))
        console_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(console_handler)

    if settings.logging.file_enabled:
        log_path = Path(settings.log_dir) / f"{settings.project_name}.log"

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=settings.logging.max_bytes,
            backupCount=settings.logging.backup_count,
            delay=True,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        file_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(file_handler)

    return logger
