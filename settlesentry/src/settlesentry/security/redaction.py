import re
from collections.abc import Mapping
from typing import Any

MASK = "*******"

SENSITIVE_KEYS = {
    "account_id",
    "accountid",
    "account_number",
    "accountnumber",
    "acct_id",
    "acctid",
    "acct_number",
    "acctnumber",
    "cardholder_name",
    "card_holder_name",
    "name_on_card",
    "card_name",
    "card_number",
    "cardnumber",
    "card",
    "cvv",
    "cvc",
    "aadhaar",
    "aadhaar_last4",
    "dob",
    "date_of_birth",
    "pincode",
    "pin_code",
    "expiry",
    "expiry_date",
    "expiry_month",
    "expiry_year",
    "expiration",
    "expiration_date",
    "expiration_month",
    "expiration_year",
}


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def is_sensitive_key(key: str) -> bool:
    return _normalize_key(key) in SENSITIVE_KEYS


ACCOUNT_ID_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:account[_\s]*(?:id|number|num|no)|acct[_\s]*(?:id|number|num|no))\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>ACC[A-Z0-9]+)"
    r"(?(quote)(?P=quote)|\b)"
)

CARDHOLDER_NAME_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:cardholder[_\s]*name|card[_\s]*holder[_\s]*name|name[_\s]*on[_\s]*card|card[_\s]*name)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[A-Za-z][A-Za-z .'-]{1,80})"
    r"(?(quote)(?P=quote))"
)

CARD_CONTEXT_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:card(?:_number|\s+number)?|credit\s+card|debit\s+card)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d[\d\s-]{11,22}\d)"
    r"(?(quote)(?P=quote))"
)

EXPIRY_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:exp(?:iry|iration)?(?:[_\s]*(?:date|month|year))?|expiry[_\s]*(?:date|month|year))\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d{1,2}\s*/\s*\d{2,4}|\d{1,2}|\d{2,4})"
    r"(?(quote)(?P=quote)|\b)"
)

CVV_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:cvv|cvc)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d{3,4})"
    r"(?(quote)(?P=quote)|\b)"
)

AADHAAR_LAST4_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:aadhaar(?:_last4|\s+last\s+4)?)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d{4})"
    r"(?(quote)(?P=quote)|\b)"
)

DOB_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:dob|date\s+of\s+birth)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d{4}-\d{2}-\d{2})"
    r"(?(quote)(?P=quote))"
)

PINCODE_RE = re.compile(
    r"(?ix)"
    r"(?P<label>\"?(?:pincode|pin\s+code)\"?)"
    r"(?P<sep>\s*(?::|=|\bis\b)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>\d{6})"
    r"(?(quote)(?P=quote)|\b)"
)

LABEL_REPLACEMENT_PATTERNS = (
    ACCOUNT_ID_RE,
    CARDHOLDER_NAME_RE,
    CARD_CONTEXT_RE,
    EXPIRY_RE,
    CVV_RE,
    AADHAAR_LAST4_RE,
    DOB_RE,
    PINCODE_RE,
)


def _mask_match(match: re.Match[str]) -> str:
    label = match.group("label")
    sep = match.group("sep")
    quote = match.group("quote") or ""
    return f"{label}{sep}{quote}{MASK}{quote}"


def redact_sensitive_value(value: Any, key_hint: str | None = None) -> Any:
    if key_hint and is_sensitive_key(key_hint):
        return MASK

    if isinstance(value, str):
        return redact_sensitive_text(value)

    if isinstance(value, Mapping):
        return {key: redact_sensitive_value(item, key_hint=str(key)) for key, item in value.items()}

    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)

    return value


def redact_sensitive_text(text: str) -> str:
    redacted = text

    for pattern in LABEL_REPLACEMENT_PATTERNS:
        redacted = pattern.sub(_mask_match, redacted)

    return redacted
