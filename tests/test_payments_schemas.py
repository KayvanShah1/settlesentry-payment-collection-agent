from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from settlesentry.integrations.payments.schemas import (
    AccountDetails,
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


def test_account_details_accepts_leap_day_dob():
    account = AccountDetails(
        account_id="ACC1004",
        full_name="Rahul Mehta",
        dob="1988-02-29",
        aadhaar_last4="1357",
        pincode="400004",
        balance=Decimal("3200.50"),
    )

    assert account.dob == "1988-02-29"


def test_account_details_rejects_invalid_non_leap_dob():
    with pytest.raises(ValidationError):
        AccountDetails(
            account_id="ACC1004",
            full_name="Rahul Mehta",
            dob="1989-02-29",
            aadhaar_last4="1357",
            pincode="400004",
            balance=Decimal("3200.50"),
        )


def test_payment_request_rejects_more_than_two_decimal_places():
    with pytest.raises(ValidationError):
        PaymentRequest(
            account_id="ACC1001",
            amount=Decimal("500.001"),
            payment_method=PaymentMethod(card=_valid_non_amex_card()),
        )


def test_payment_request_accepts_partial_payment_amount():
    payment = PaymentRequest(
        account_id="ACC1001",
        amount=Decimal("500.00"),
        payment_method=PaymentMethod(card=_valid_non_amex_card()),
    )

    assert payment.amount == Decimal("500.00")


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


def test_amex_rejects_three_digit_cvv():
    with pytest.raises(ValidationError, match="Amex cards require a 4-digit CVV"):
        CardDetails(
            cardholder_name="Nithin Jain",
            card_number="378282246310005",
            cvv="123",
            expiry_month=12,
            expiry_year=date.today().year + 1,
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


def test_card_number_with_spaces_and_hyphens_is_normalized():
    card = CardDetails(
        cardholder_name="Nithin Jain",
        card_number="4532 0151-1283 0366",
        cvv="123",
        expiry_month=12,
        expiry_year=date.today().year + 1,
    )

    assert card.card_number == "4532015112830366"
