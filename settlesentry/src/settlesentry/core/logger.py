"""
Logging setup with two guarantees:
1) sensitive values are redacted before emission
2) custom context fields are surfaced for traceability
"""

import logging
import logging.handlers
from pathlib import Path

from rich.logging import RichHandler

from settlesentry.core.settings import settings
from settlesentry.security.redaction import (
    redact_sensitive_text,
    redact_sensitive_value,
)

LOG_RECORD_BUILTIN_KEYS = set(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
)
CONTEXT_EXCLUDED_KEYS = LOG_RECORD_BUILTIN_KEYS | {"message", "asctime"}


class ContextAwareFormatter(logging.Formatter):
    """
    Appends custom `extra` fields to formatted log lines for traceability.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)

        context_parts: list[str] = []
        for key in sorted(record.__dict__.keys()):
            if key in CONTEXT_EXCLUDED_KEYS:
                continue
            context_parts.append(f"{key}={record.__dict__[key]}")

        if not context_parts:
            return base

        return f"{base} | {' '.join(context_parts)}"


class SensitiveDataFilter(logging.Filter):
    """
    Redacts sensitive data before records reach console or file handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the rendered message.
        message = record.getMessage()
        record.msg = redact_sensitive_text(message)
        record.args = ()

        # Redact custom extra fields.
        for key, value in list(record.__dict__.items()):
            if key in LOG_RECORD_BUILTIN_KEYS:
                continue

            record.__dict__[key] = redact_sensitive_value(value, key_hint=key)

        return True


def _get_log_level(level_name: str) -> int:
    """Resolve configured level name into a logging module constant."""
    return getattr(logging, level_name.upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured logger singleton for `name`.

    The logger includes redaction and optional console/file handlers based on settings.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = _get_log_level(settings.logging.level)
    redaction_filter = SensitiveDataFilter()

    logger.setLevel(log_level)
    logger.propagate = False
    logger.addFilter(redaction_filter)

    if settings.logging.console_enabled:
        console_handler = RichHandler(rich_tracebacks=True)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(ContextAwareFormatter("%(name)s - %(message)s"))
        console_handler.addFilter(redaction_filter)
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
        file_handler.setFormatter(ContextAwareFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        file_handler.addFilter(redaction_filter)
        logger.addHandler(file_handler)

    return logger
