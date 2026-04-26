import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "card_number",
    "cvv",
    "cvc",
    "aadhaar",
    "aadhaar_last4",
    "dob",
    "date_of_birth",
    "pincode",
    "pin_code",
}


CARD_CONTEXT_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:card(?:_number|\s+number)?|credit\s+card|debit\s+card)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d[\d\s-]{11,22}\d)"
    r"(?P=quote)?"
)

CVV_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:cvv|cvc)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"\d{3,4}"
    r"(?P=quote)?"
    r"\b"
)

AADHAAR_LAST4_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:aadhaar(?:_last4|\s+last\s+4)?)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"\d{4}"
    r"(?P=quote)?"
    r"\b"
)

DOB_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:dob|date\s+of\s+birth)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"\d{4}-\d{2}-\d{2}"
    r"(?P=quote)?"
)

PINCODE_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:pincode|pin\s+code)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"\d{6}"
    r"(?P=quote)?"
    r"\b"
)


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def luhn_valid(value: str) -> bool:
    digits = digits_only(value)

    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    parity = len(digits) % 2

    for index, char in enumerate(digits):
        digit = int(char)

        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9

        checksum += digit

    return checksum % 10 == 0


def _mask_match(match: re.Match[str], mask: str) -> str:
    label = match.group("label")
    sep = match.group("sep")
    quote = match.group("quote") or ""

    return f"{label}{sep}{quote}{mask}{quote}"


def _redact_card(match: re.Match[str]) -> str:
    # In card-labeled contexts we always redact to avoid leaking PAN-like values.
    return _mask_match(match, "[REDACTED_CARD]")


def redact_sensitive_text(text: str) -> str:
    redacted = CARD_CONTEXT_RE.sub(_redact_card, text)
    redacted = CVV_RE.sub(lambda match: _mask_match(match, "[REDACTED_CVV]"), redacted)
    redacted = AADHAAR_LAST4_RE.sub(
        lambda match: _mask_match(match, "[REDACTED_AADHAAR_LAST4]"),
        redacted,
    )
    redacted = DOB_RE.sub(lambda match: _mask_match(match, "[REDACTED_DOB]"), redacted)
    redacted = PINCODE_RE.sub(
        lambda match: _mask_match(match, "[REDACTED_PINCODE]"),
        redacted,
    )

    return redacted


def redact_sensitive_value(value: Any, key_hint: str | None = None) -> Any:
    if key_hint and key_hint.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"

    if isinstance(value, str):
        return redact_sensitive_text(value)

    if isinstance(value, Mapping):
        return {key: redact_sensitive_value(item, key_hint=str(key)) for key, item in value.items()}

    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)

    return value
