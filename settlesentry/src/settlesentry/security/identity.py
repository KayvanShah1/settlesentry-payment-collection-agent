from __future__ import annotations

import re
from datetime import date
from typing import Any


def normalize_optional_identity_text(value: Any) -> str | None:
    """Convert identity-like inputs to text while preserving missing values."""
    if value in (None, ""):
        return None

    return str(value)


def validate_iso_date(value: str) -> str:
    """Validate strict ISO date text (YYYY-MM-DD)."""
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must be valid and use YYYY-MM-DD format") from exc

    return value


def validate_fixed_digits(value: str, *, digits: int, field_name: str) -> str:
    """Validate a numeric string with an exact fixed length."""
    if not re.fullmatch(rf"\d{{{digits}}}", value):
        raise ValueError(f"{field_name} must be exactly {digits} digits")

    return value
