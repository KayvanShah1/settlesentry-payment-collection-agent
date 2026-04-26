import logging

from settlesentry.core.logger import SensitiveDataFilter
from settlesentry.security.redaction import (
    luhn_valid,
    redact_sensitive_text,
    redact_sensitive_value,
)


def test_luhn_valid_card():
    assert luhn_valid("4532015112830366") is True


def test_luhn_invalid_card():
    assert luhn_valid("4532015112830367") is False


def test_card_number_is_redacted_in_context():
    raw = "card_number=4532015112830366"
    assert redact_sensitive_text(raw) == "card_number=[REDACTED_CARD]"


def test_invalid_card_number_is_redacted_when_labeled():
    raw = "card_number=1234567890123456"
    assert redact_sensitive_text(raw) == "card_number=[REDACTED_CARD]"


def test_large_decimal_amount_is_not_redacted_as_card():
    raw = "amount=17693696986376890.90"
    assert redact_sensitive_text(raw) == raw


def test_cvv_is_redacted():
    raw = "cvv=123"
    assert redact_sensitive_text(raw) == "cvv=[REDACTED_CVV]"


def test_identity_fields_are_redacted_when_labeled():
    raw = "dob=1990-05-14 aadhaar_last4=4321 pincode=400001"

    expected = "dob=[REDACTED_DOB] aadhaar_last4=[REDACTED_AADHAAR_LAST4] pincode=[REDACTED_PINCODE]"

    assert redact_sensitive_text(raw) == expected


def test_nested_payload_sensitive_keys_are_redacted():
    payload = {
        "account_id": "ACC1001",
        "amount": 500.00,
        "card_number": "4532015112830366",
        "cvv": "123",
    }

    assert redact_sensitive_value(payload) == {
        "account_id": "ACC1001",
        "amount": 500.00,
        "card_number": "[REDACTED]",
        "cvv": "[REDACTED]",
    }


def test_logging_filter_redacts_rendered_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="card_number=4532015112830366 cvv=123",
        args=(),
        exc_info=None,
    )

    SensitiveDataFilter().filter(record)

    assert record.msg == "card_number=[REDACTED_CARD] cvv=[REDACTED_CVV]"
    assert record.args == ()


def test_logging_filter_redacts_extra_fields():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe message",
        args=(),
        exc_info=None,
    )
    record.card_number = "4532015112830366"
    record.payload = {
        "cvv": "123",
        "metadata": "dob=1990-05-14",
    }

    SensitiveDataFilter().filter(record)

    assert record.card_number == "[REDACTED]"
    assert record.payload["cvv"] == "[REDACTED]"
    assert record.payload["metadata"] == "dob=[REDACTED_DOB]"
