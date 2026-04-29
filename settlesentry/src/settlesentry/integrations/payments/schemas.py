from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum, auto
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from settlesentry.security.cards import digits_only, luhn_valid
from settlesentry.security.identity import validate_iso_date


class Patterns:
    AADHAAR_LAST4 = r"^\d{4}$"
    PINCODE = r"^\d{6}$"
    CVV = r"^\d{3,4}$"
    CARD_ALLOWED_CHARS = r"[\d\s-]+"


class PaymentsAPIErrorCode(StrEnum):
    """Canonical error codes returned by lookup/payment flows."""

    ACCOUNT_NOT_FOUND = auto()
    INVALID_AMOUNT = auto()
    INSUFFICIENT_BALANCE = auto()
    INVALID_CARD = auto()
    INVALID_CVV = auto()
    INVALID_EXPIRY = auto()
    NETWORK_ERROR = auto()
    TIMEOUT = auto()
    INVALID_RESPONSE = auto()
    UNEXPECTED_STATUS = auto()

    def default_message(self) -> str:
        return PAYMENT_ERROR_MESSAGES.get(
            self,
            "The payment could not be processed.",
        )


PAYMENT_ERROR_MESSAGES: dict[PaymentsAPIErrorCode, str] = {
    PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND: "No account found with the provided account ID.",
    PaymentsAPIErrorCode.INVALID_AMOUNT: "The payment amount is invalid.",
    PaymentsAPIErrorCode.INSUFFICIENT_BALANCE: "The payment amount exceeds the outstanding balance.",
    PaymentsAPIErrorCode.INVALID_CARD: "The card number appears to be invalid.",
    PaymentsAPIErrorCode.INVALID_CVV: "The CVV appears to be invalid.",
    PaymentsAPIErrorCode.INVALID_EXPIRY: "The card expiry appears to be invalid or expired.",
    PaymentsAPIErrorCode.NETWORK_ERROR: "The payment service is currently unreachable.",
    PaymentsAPIErrorCode.TIMEOUT: "The payment service took too long to respond.",
    PaymentsAPIErrorCode.INVALID_RESPONSE: "The payment service returned an invalid response.",
    PaymentsAPIErrorCode.UNEXPECTED_STATUS: "The payment service returned an unexpected response.",
}


def parse_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def validate_money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Amount must be finite.")

    if value <= Decimal("0"):
        raise ValueError("Amount must be greater than 0.")

    if value.as_tuple().exponent < -2:
        raise ValueError("Amount must have at most 2 decimal places.")

    return value


class PaymentAPIModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class AccountLookupRequest(PaymentAPIModel):
    """Request payload for account lookup tool call."""

    account_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Opaque account identifier provided by the user.",
        examples=["ACC1001"],
    )


class AccountDetails(PaymentAPIModel):
    """Normalized account details returned by successful account lookup."""

    account_id: str = Field(..., min_length=1, max_length=64)
    full_name: str = Field(..., min_length=1, max_length=100)
    dob: str = Field(..., description="Date of birth in YYYY-MM-DD format.")
    aadhaar_last4: str = Field(..., pattern=Patterns.AADHAAR_LAST4)
    pincode: str = Field(..., pattern=Patterns.PINCODE)
    balance: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, value: str) -> str:
        return validate_iso_date(value)

    @field_validator("balance", mode="before")
    @classmethod
    def parse_balance(cls, value: object) -> Decimal:
        return parse_decimal(value)

    @field_serializer("balance")
    def serialize_balance(self, value: Decimal) -> float:
        return float(value)


class CardDetails(PaymentAPIModel):
    """Card fields with structural and business-rule validation."""

    cardholder_name: str = Field(..., min_length=1, max_length=100)
    card_number: str = Field(
        ...,
        min_length=13,
        max_length=23,
        description="Card number with digits, spaces, or hyphens.",
    )
    cvv: str = Field(..., pattern=Patterns.CVV)
    expiry_month: int = Field(..., ge=1, le=12)
    expiry_year: int = Field(..., ge=2000, le=2100)

    @field_validator("card_number")
    @classmethod
    def validate_card_number(cls, value: str) -> str:
        digits = digits_only(value)

        if digits != value and not re.fullmatch(Patterns.CARD_ALLOWED_CHARS, value):
            raise ValueError("Card number contains invalid characters")

        if not 13 <= len(digits) <= 19:
            raise ValueError("Card number must contain 13 to 19 digits")

        if not luhn_valid(digits):
            raise ValueError("Card number failed Luhn validation")

        return digits

    @model_validator(mode="after")
    def validate_card_consistency(self) -> "CardDetails":
        is_amex = self.card_number.startswith(("34", "37")) and len(self.card_number) == 15

        if is_amex and len(self.cvv) != 4:
            raise ValueError("Amex cards require a 4-digit CVV")

        if not is_amex and len(self.cvv) != 3:
            raise ValueError("Non-Amex cards require a 3-digit CVV")

        today = date.today()
        if (self.expiry_year, self.expiry_month) < (today.year, today.month):
            raise ValueError("Card has expired")

        return self


class PaymentMethod(PaymentAPIModel):
    type: Literal["card"] = "card"
    card: CardDetails


class PaymentRequest(PaymentAPIModel):
    """Request payload for payment processing tool call."""

    account_id: str = Field(..., min_length=1, max_length=64)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    payment_method: PaymentMethod

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: object) -> Decimal:
        return parse_decimal(value)

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> float:
        return float(value)


class PaymentSuccessResponse(PaymentAPIModel):
    success: Literal[True]
    transaction_id: str = Field(..., min_length=1)


class PaymentFailureResponse(PaymentAPIModel):
    success: Literal[False]
    error_code: PaymentsAPIErrorCode


class AccountLookupError(PaymentAPIModel):
    error_code: Literal[PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND]
    message: str


class LookupResult(PaymentAPIModel):
    ok: bool
    account: AccountDetails | None = None
    error_code: PaymentsAPIErrorCode | None = None
    message: str | None = None
    status_code: int | None = None


class PaymentResult(PaymentAPIModel):
    ok: bool
    transaction_id: str | None = None
    error_code: PaymentsAPIErrorCode | None = None
    message: str | None = None
    status_code: int | None = None
