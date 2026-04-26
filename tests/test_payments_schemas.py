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
