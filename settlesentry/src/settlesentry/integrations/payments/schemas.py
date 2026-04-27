"""
Pydantic models and validators for payment API contracts.

These schemas keep tool payloads strict, predictable, and easy to inspect.
"""

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
from settlesentry.security.identity import validate_fixed_digits, validate_iso_date

ACCOUNT_ID_RE = re.compile(r"^ACC\d+$")


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


def validate_money(value: Decimal) -> Decimal:
    """Validate positive currency amount with at most two decimal places."""
    if value <= Decimal("0"):
        raise ValueError("Amount must be greater than zero")

    if value.as_tuple().exponent < -2:
        raise ValueError("Amount cannot have more than 2 decimal places")

    return value


def parse_decimal(value: object) -> Decimal:
    """Coerce numeric-like values into Decimal without float precision loss."""
    return Decimal(str(value))


def validate_account_id_format(
    value: str,
    *,
    error_message: str = "Account ID must look like ACC1001",
) -> str:
    """Validate canonical account ID text in ACC<digits> format."""
    if not ACCOUNT_ID_RE.fullmatch(value):
        raise ValueError(error_message)

    return value


class AccountLookupRequest(BaseModel):
    """Request payload for account lookup tool call."""

    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: str = Field(..., description="Account ID to look up")

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return validate_account_id_format(value)


class AccountDetails(BaseModel):
    """Normalized account details returned by successful account lookup."""

    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: Decimal

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return validate_account_id_format(
            value,
            error_message="Invalid account_id in API response",
        )

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, value: str) -> str:
        return validate_iso_date(value)

    @field_validator("aadhaar_last4")
    @classmethod
    def validate_aadhaar_last4(cls, value: str) -> str:
        return validate_fixed_digits(value, digits=4, field_name="aadhaar_last4")

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, value: str) -> str:
        return validate_fixed_digits(value, digits=6, field_name="pincode")

    @field_validator("balance", mode="before")
    @classmethod
    def parse_balance(cls, value: object) -> Decimal:
        return parse_decimal(value)

    @field_serializer("balance")
    def serialize_balance(self, value: Decimal) -> float:
        return float(value)


class AccountLookupError(BaseModel):
    """Typed shape for known account lookup failure payloads."""

    error_code: Literal[PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND]
    message: str


class CardDetails(BaseModel):
    """Card fields with structural and business-rule validation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    cardholder_name: str
    card_number: str
    cvv: str
    expiry_month: int
    expiry_year: int

    @field_validator("cardholder_name")
    @classmethod
    def validate_cardholder_name(cls, value: str) -> str:
        if not value:
            raise ValueError("cardholder_name is required")

        return value

    @field_validator("card_number")
    @classmethod
    def validate_card_number(cls, value: str) -> str:
        digits = digits_only(value)

        if digits != value and not re.fullmatch(r"[\d\s-]+", value):
            raise ValueError("Card number contains invalid characters")

        if not 13 <= len(digits) <= 19:
            raise ValueError("Card number must contain 13 to 19 digits")

        if not luhn_valid(digits):
            raise ValueError("Card number failed Luhn validation")

        return digits

    @field_validator("cvv")
    @classmethod
    def validate_cvv_digits(cls, value: str) -> str:
        if not re.fullmatch(r"\d{3,4}", value):
            raise ValueError("CVV must be 3 or 4 digits")

        return value

    @field_validator("expiry_month")
    @classmethod
    def validate_expiry_month(cls, value: int) -> int:
        if not 1 <= value <= 12:
            raise ValueError("expiry_month must be between 1 and 12")

        return value

    @field_validator("expiry_year")
    @classmethod
    def validate_expiry_year(cls, value: int) -> int:
        if value < 2000:
            raise ValueError("expiry_year must be a four-digit year")

        return value

    @model_validator(mode="after")
    def validate_card_consistency(self) -> CardDetails:
        is_amex = self.card_number.startswith(("34", "37")) and len(self.card_number) == 15

        if is_amex and len(self.cvv) != 4:
            raise ValueError("Amex cards require a 4-digit CVV")

        if not is_amex and len(self.cvv) != 3:
            raise ValueError("Non-Amex cards require a 3-digit CVV")

        today = date.today()
        if (self.expiry_year, self.expiry_month) < (today.year, today.month):
            raise ValueError("Card has expired")

        return self


class PaymentMethod(BaseModel):
    """Payment method envelope currently supporting card payments only."""

    type: Literal["card"] = "card"
    card: CardDetails


class PaymentRequest(BaseModel):
    """Request payload for payment processing tool call."""

    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: str
    amount: Decimal
    payment_method: PaymentMethod

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return validate_account_id_format(value)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: object) -> Decimal:
        return parse_decimal(value)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return validate_money(value)

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> float:
        return float(value)


class PaymentSuccessResponse(BaseModel):
    """Expected successful payment API payload shape."""

    success: Literal[True]
    transaction_id: str


class PaymentFailureResponse(BaseModel):
    """Expected failed payment API payload shape."""

    success: Literal[False]
    error_code: PaymentsAPIErrorCode


class LookupResult(BaseModel):
    """Internal normalized outcome for lookup step."""

    ok: bool
    account: AccountDetails | None = None
    error_code: PaymentsAPIErrorCode | None = None
    message: str | None = None
    status_code: int | None = None


class PaymentResult(BaseModel):
    """Internal normalized outcome for payment step."""

    ok: bool
    transaction_id: str | None = None
    error_code: PaymentsAPIErrorCode | None = None
    message: str | None = None
    status_code: int | None = None
