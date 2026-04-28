import logging

import pytest

from settlesentry.core.logger import SensitiveDataFilter
from settlesentry.security.cards import luhn_valid
from settlesentry.security.redaction import MASK, is_sensitive_key, redact_sensitive_text, redact_sensitive_value


def test_luhn_valid_card():
    assert luhn_valid("4532015112830366") is True


def test_luhn_invalid_card():
    assert luhn_valid("4532015112830367") is False


def test_sensitive_key_detection_normalizes_common_variants():
    assert is_sensitive_key("card_number") is True
    assert is_sensitive_key("Card Number") is True
    assert is_sensitive_key("card-number") is True
    assert is_sensitive_key("cvv") is True
    assert is_sensitive_key("aadhaar_last4") is True
    assert is_sensitive_key("pin_code") is True
    assert is_sensitive_key("account_id") is False
    assert is_sensitive_key("amount") is False


SENSITIVE_TEXT_CASES = (
    ("card_number=4532015112830366", f"card_number={MASK}"),
    ("card number is 4532 0151 1283 0366", f"card number is {MASK}"),
    ("credit card: 4532-0151-1283-0366", f"credit card: {MASK}"),
    ("cvv=123", f"cvv={MASK}"),
    ("cvc is '1234'", f"cvc is '{MASK}'"),
    ('"cvv"="123"', f'"cvv"="{MASK}"'),
    ("aadhaar_last4=4321", f"aadhaar_last4={MASK}"),
    ("aadhaar last 4 is 4321", f"aadhaar last 4 is {MASK}"),
    ("dob=1990-05-14", f"dob={MASK}"),
    ("date of birth is 1990-05-14", f"date of birth is {MASK}"),
    ("pincode=400001", f"pincode={MASK}"),
    ("pin code is 400001", f"pin code is {MASK}"),
    (
        "card_number=4532015112830366 cvv=123 aadhaar_last4=4321 dob=1990-05-14 pincode=400001",
        f"card_number={MASK} cvv={MASK} aadhaar_last4={MASK} dob={MASK} pincode={MASK}",
    ),
)


@pytest.mark.parametrize(("raw", "expected"), SENSITIVE_TEXT_CASES)
def test_redact_sensitive_text_masks_labeled_sensitive_fields(raw: str, expected: str):
    assert redact_sensitive_text(raw) == expected


def test_redact_sensitive_text_does_not_over_redact_non_sensitive_numeric_values():
    raw = "amount=17693696986376890.90"

    assert redact_sensitive_text(raw) == raw


def test_nested_payload_sensitive_keys_are_fully_masked():
    payload = {
        "account_id": "ACC1001",
        "amount": 500.00,
        "card_number": "4532015112830366",
        "cvv": "123",
        "identity": {
            "dob": "1990-05-14",
            "aadhaar_last4": "4321",
            "pincode": "400001",
        },
    }

    assert redact_sensitive_value(payload) == {
        "account_id": "ACC1001",
        "amount": 500.00,
        "card_number": MASK,
        "cvv": MASK,
        "identity": {
            "dob": MASK,
            "aadhaar_last4": MASK,
            "pincode": MASK,
        },
    }


def test_redact_sensitive_value_preserves_safe_strings():
    payload = {
        "account_id": "ACC1001",
        "message": "safe operational message",
        "status_code": 200,
        "amount": 500.00,
    }

    assert redact_sensitive_value(payload) == payload


def test_redact_sensitive_value_handles_lists_and_tuples():
    payload = {
        "events": [
            {"cvv": "123"},
            {"account_id": "ACC1001"},
        ],
        "cards": (
            {"card_number": "4532015112830366"},
            {"amount": 500.00},
        ),
    }

    redacted = redact_sensitive_value(payload)

    assert redacted["events"][0]["cvv"] == MASK
    assert redacted["events"][1]["account_id"] == "ACC1001"
    assert redacted["cards"][0]["card_number"] == MASK
    assert redacted["cards"][1]["amount"] == 500.00


@pytest.mark.parametrize(("raw", "expected"), SENSITIVE_TEXT_CASES)
def test_logging_filter_redacts_rendered_message_text(raw: str, expected: str):
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=raw,
        args=(),
        exc_info=None,
    )

    SensitiveDataFilter().filter(record)

    assert record.msg == expected
    assert record.args == ()


def test_logging_filter_masks_sensitive_extra_fields():
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
    record.cvv = "123"
    record.payload = {
        "dob": "1990-05-14",
        "aadhaar_last4": "4321",
        "pincode": "400001",
        "metadata": "safe metadata",
    }

    SensitiveDataFilter().filter(record)

    assert record.card_number == MASK
    assert record.cvv == MASK
    assert record.payload["dob"] == MASK
    assert record.payload["aadhaar_last4"] == MASK
    assert record.payload["pincode"] == MASK
    assert record.payload["metadata"] == "safe metadata"


def test_logging_filter_preserves_safe_extra_fields():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe message",
        args=(),
        exc_info=None,
    )
    record.account_id = "ACC1001"
    record.amount = 500.00
    record.status_code = 200

    SensitiveDataFilter().filter(record)

    assert record.account_id == "ACC1001"
    assert record.amount == 500.00
    assert record.status_code == 200
