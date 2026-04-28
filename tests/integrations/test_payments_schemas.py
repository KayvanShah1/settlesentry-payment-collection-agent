from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    AccountLookupRequest,
    CardDetails,
    PaymentMethod,
    PaymentRequest,
)


def _valid_non_amex_card() -> CardDetails:
    return CardDetails(
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        cvv="123",
        expiry_month=12,
        expiry_year=date.today().year + 1,
    )


def _valid_account_details(**overrides) -> AccountDetails:
    data = {
        "account_id": "ACC1004",
        "full_name": "Rahul Mehta",
        "dob": "1988-02-29",
        "aadhaar_last4": "1357",
        "pincode": "400004",
        "balance": Decimal("3200.50"),
    }
    data.update(overrides)
    return AccountDetails(**data)


def test_account_lookup_request_accepts_valid_account_id():
    request = AccountLookupRequest(account_id=" ACC1001 ")

    assert request.account_id == "ACC1001"


def test_account_lookup_request_rejects_invalid_account_id():
    with pytest.raises(ValidationError):
        AccountLookupRequest(account_id="BAD1001")


def test_account_details_accepts_valid_payload():
    account = _valid_account_details()

    assert account.account_id == "ACC1004"
    assert account.full_name == "Rahul Mehta"
    assert account.dob == "1988-02-29"
    assert account.aadhaar_last4 == "1357"
    assert account.pincode == "400004"
    assert account.balance == Decimal("3200.50")


def test_account_details_rejects_invalid_non_leap_dob():
    with pytest.raises(ValidationError):
        _valid_account_details(dob="1989-02-29")


def test_account_details_rejects_invalid_account_id():
    with pytest.raises(ValidationError):
        _valid_account_details(account_id="BAD1004")


def test_account_details_rejects_invalid_aadhaar_last4():
    with pytest.raises(ValidationError):
        _valid_account_details(aadhaar_last4="135")


def test_account_details_rejects_non_numeric_aadhaar_last4():
    with pytest.raises(ValidationError):
        _valid_account_details(aadhaar_last4="13A7")


def test_account_details_rejects_invalid_pincode():
    with pytest.raises(ValidationError):
        _valid_account_details(pincode="40004")


def test_account_details_rejects_non_numeric_pincode():
    with pytest.raises(ValidationError):
        _valid_account_details(pincode="400A04")


def test_account_details_rejects_negative_balance():
    with pytest.raises(ValidationError):
        _valid_account_details(balance=Decimal("-1.00"))


def test_account_details_rejects_balance_with_more_than_two_decimal_places():
    with pytest.raises(ValidationError):
        _valid_account_details(balance=Decimal("3200.501"))


def test_payment_request_accepts_valid_amount():
    payment = PaymentRequest(
        account_id="ACC1001",
        amount=Decimal("500.00"),
        payment_method=PaymentMethod(card=_valid_non_amex_card()),
    )

    assert payment.amount == Decimal("500.00")


def test_payment_request_accepts_numeric_string_amount():
    payment = PaymentRequest(
        account_id="ACC1001",
        amount="500.00",
        payment_method=PaymentMethod(card=_valid_non_amex_card()),
    )

    assert payment.amount == Decimal("500.00")


def test_payment_request_rejects_zero_amount():
    with pytest.raises(ValidationError):
        PaymentRequest(
            account_id="ACC1001",
            amount=Decimal("0"),
            payment_method=PaymentMethod(card=_valid_non_amex_card()),
        )


def test_payment_request_rejects_negative_amount():
    with pytest.raises(ValidationError):
        PaymentRequest(
            account_id="ACC1001",
            amount=Decimal("-10.00"),
            payment_method=PaymentMethod(card=_valid_non_amex_card()),
        )


def test_payment_request_rejects_more_than_two_decimal_places():
    with pytest.raises(ValidationError):
        PaymentRequest(
            account_id="ACC1001",
            amount=Decimal("500.001"),
            payment_method=PaymentMethod(card=_valid_non_amex_card()),
        )


def test_payment_request_rejects_invalid_account_id():
    with pytest.raises(ValidationError):
        PaymentRequest(
            account_id="BAD1001",
            amount=Decimal("500.00"),
            payment_method=PaymentMethod(card=_valid_non_amex_card()),
        )


def test_card_accepts_valid_non_amex():
    card = _valid_non_amex_card()

    assert card.card_number == "4532015112830366"
    assert card.cvv == "123"


def test_cardholder_name_is_trimmed():
    card = CardDetails(
        cardholder_name=" Nithin Jain ",
        card_number="4532015112830366",
        cvv="123",
        expiry_month=12,
        expiry_year=date.today().year + 1,
    )

    assert card.cardholder_name == "Nithin Jain"


def test_card_rejects_empty_cardholder_name():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="",
            card_number="4532015112830366",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_number_with_spaces_and_hyphens_is_normalized():
    card = CardDetails(
        cardholder_name="Nithin Jain",
        card_number="4532 0151-1283 0366",
        cvv="123",
        expiry_month=12,
        expiry_year=date.today().year + 1,
    )

    assert card.card_number == "4532015112830366"


def test_card_rejects_invalid_characters():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532-0151-1283-ABCD",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_invalid_luhn_number():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830367",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_short_card_number():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="123456789012",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_invalid_cvv_length():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="12",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_non_numeric_cvv():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="12A",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_non_amex_rejects_four_digit_cvv():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="1234",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_amex_accepts_four_digit_cvv():
    card = CardDetails(
        cardholder_name="Nithin Jain",
        card_number="378282246310005",
        cvv="1234",
        expiry_month=12,
        expiry_year=date.today().year + 1,
    )

    assert card.card_number == "378282246310005"
    assert card.cvv == "1234"


def test_amex_rejects_three_digit_cvv():
    with pytest.raises(ValidationError, match="Amex cards require a 4-digit CVV"):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="378282246310005",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_invalid_expiry_month():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="123",
            expiry_month=13,
            expiry_year=date.today().year + 1,
        )


def test_card_rejects_invalid_expiry_year():
    with pytest.raises(ValidationError):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="123",
            expiry_month=12,
            expiry_year=1999,
        )


def test_card_expiry_current_month_is_valid():
    today = date.today()

    card = CardDetails(
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        cvv="123",
        expiry_month=today.month,
        expiry_year=today.year,
    )

    assert card.expiry_month == today.month
    assert card.expiry_year == today.year


def test_card_expiry_previous_month_is_rejected():
    today = date.today()
    expiry_year = today.year
    expiry_month = today.month - 1

    if today.month == 1:
        expiry_year = today.year - 1
        expiry_month = 12

    with pytest.raises(ValidationError, match="Card has expired"):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="4532015112830366",
            cvv="123",
            expiry_month=expiry_month,
            expiry_year=expiry_year,
        )


def test_payment_method_defaults_to_card_type():
    payment_method = PaymentMethod(card=_valid_non_amex_card())

    assert payment_method.type == "card"
