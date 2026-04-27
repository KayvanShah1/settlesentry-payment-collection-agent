from __future__ import annotations

from decimal import Decimal
from enum import StrEnum, auto
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    CardDetails,
    PaymentMethod,
    PaymentRequest,
    parse_decimal,
    validate_money,
)
from settlesentry.security.identity import (
    normalize_optional_identity_text,
    validate_fixed_digits,
    validate_iso_date,
)
from settlesentry.security.cards import digits_only

EXTRACTED_TO_STATE_FIELD_MAP = {
    "account_id": "account_id",
    "full_name": "provided_full_name",
    "dob": "provided_dob",
    "aadhaar_last4": "provided_aadhaar_last4",
    "pincode": "provided_pincode",
    "payment_amount": "payment_amount",
    "cardholder_name": "cardholder_name",
    "card_number": "card_number",
    "cvv": "cvv",
    "expiry_month": "expiry_month",
    "expiry_year": "expiry_year",
}

PAYMENT_INPUT_FIELDS = frozenset(
    {
        "payment_amount",
        "cardholder_name",
        "card_number",
        "cvv",
        "expiry_month",
        "expiry_year",
    }
)

SECONDARY_FACTOR_FIELD_MAP = (
    ("provided_dob", "dob"),
    ("provided_aadhaar_last4", "aadhaar_last4"),
    ("provided_pincode", "pincode"),
)

REQUIRED_CARD_FIELD_NAMES = (
    "cardholder_name",
    "card_number",
    "cvv",
    "expiry_month",
    "expiry_year",
)


class ConversationStep(StrEnum):
    START = auto()

    WAITING_FOR_ACCOUNT_ID = auto()
    LOOKING_UP_ACCOUNT = auto()

    WAITING_FOR_FULL_NAME = auto()
    WAITING_FOR_SECONDARY_FACTOR = auto()
    VERIFIED = auto()

    WAITING_FOR_PAYMENT_AMOUNT = auto()
    WAITING_FOR_CARDHOLDER_NAME = auto()
    WAITING_FOR_CARD_NUMBER = auto()
    WAITING_FOR_CVV = auto()
    WAITING_FOR_EXPIRY = auto()

    WAITING_FOR_PAYMENT_CONFIRMATION = auto()
    PROCESSING_PAYMENT = auto()

    PAYMENT_SUCCESS = auto()
    CLOSED = auto()


class ExtractedUserInput(BaseModel):
    """
    Structured user input extracted from regex, LLM, or both.

    This schema is softer than the payment API schemas because users can
    provide partial information across multiple turns.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    intent: UserIntent = UserIntent.UNKNOWN
    proposed_action: ProposedAction = ProposedAction.NONE

    account_id: str | None = None

    full_name: str | None = None
    dob: str | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None

    payment_amount: Decimal | None = None

    cardholder_name: str | None = None
    card_number: str | None = None
    cvv: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None

    confirmation: bool | None = Field(
        default=None,
        description="True only when the user explicitly confirms payment.",
    )

    @field_validator("dob", "aadhaar_last4", "pincode", mode="before")
    @classmethod
    def parse_identity_fields(cls, value: Any) -> str | None:
        return normalize_optional_identity_text(value)

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, value: str | None) -> str | None:
        if value is None:
            return None

        return validate_iso_date(value)

    @field_validator("aadhaar_last4")
    @classmethod
    def validate_aadhaar_last4(cls, value: str | None) -> str | None:
        if value is None:
            return None

        return validate_fixed_digits(value, digits=4, field_name="aadhaar_last4")

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, value: str | None) -> str | None:
        if value is None:
            return None

        return validate_fixed_digits(value, digits=6, field_name="pincode")

    @field_validator("payment_amount", mode="before")
    @classmethod
    def parse_payment_amount(cls, value: Any) -> Decimal | None:
        if value in (None, ""):
            return None

        return parse_decimal(value)

    @field_validator("card_number", mode="before")
    @classmethod
    def normalize_card_number(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None

        text = str(value).strip()
        if not text:
            return None

        if all(char.isdigit() or char in {" ", "-"} for char in text):
            digits = digits_only(text)
            return digits if digits else None

        return text

    @field_validator("payment_amount")
    @classmethod
    def validate_payment_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None

        validated = validate_money(value)
        return validated.quantize(Decimal("0.01"))


class ConversationState(BaseModel):
    """
    In-memory state for a single Agent instance.

    The evaluator calls Agent.next() repeatedly on the same object, so this
    model acts as the conversation memory.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    step: ConversationStep = ConversationStep.START

    account_id: str | None = None
    account: AccountDetails | None = None

    provided_full_name: str | None = None
    provided_dob: str | None = None
    provided_aadhaar_last4: str | None = None
    provided_pincode: str | None = None

    verified: bool = False
    verification_attempts: int = 0

    payment_amount: Decimal | None = None
    cardholder_name: str | None = None
    card_number: str | None = None
    cvv: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None

    payment_confirmed: bool = False
    payment_attempts: int = 0
    transaction_id: str | None = None

    last_error: str | None = None
    completed: bool = False

    event_log: list[str] = Field(default_factory=list)

    def merge(self, extracted: ExtractedUserInput) -> None:
        """
        Merge newly extracted user fields without clearing existing context.

        Any change to payment details invalidates an earlier payment confirmation.
        """

        for source_field, target_field in EXTRACTED_TO_STATE_FIELD_MAP.items():
            value = getattr(extracted, source_field)

            if value is None:
                continue

            setattr(self, target_field, value)

            if source_field in PAYMENT_INPUT_FIELDS:
                self.payment_confirmed = False

        if extracted.confirmation is not None:
            self.payment_confirmed = extracted.confirmation

    def has_account_loaded(self) -> bool:
        return self.account is not None

    def has_secondary_factor(self) -> bool:
        return any(self.secondary_factor_values())

    def secondary_factor_values(self) -> tuple[str | None, ...]:
        """Return all user-provided secondary verification fields in fixed order."""
        return tuple(
            getattr(self, state_field)
            for state_field, _ in SECONDARY_FACTOR_FIELD_MAP
        )

    def has_matching_secondary_factor(self) -> bool:
        """Return True if any provided secondary factor matches loaded account data."""
        if not self.account:
            return False

        for state_field, account_field in SECONDARY_FACTOR_FIELD_MAP:
            provided_value = getattr(self, state_field)

            if provided_value and provided_value == getattr(self.account, account_field):
                return True

        return False

    def has_complete_card_fields(self) -> bool:
        return all(getattr(self, field_name) for field_name in REQUIRED_CARD_FIELD_NAMES)

    def outstanding_balance(self) -> Decimal | None:
        if not self.account:
            return None

        return self.account.balance

    def card_last4(self) -> str | None:
        if not self.card_number:
            return None

        digits = digits_only(self.card_number)
        return digits[-4:] if len(digits) >= 4 else None

    def build_card_details(self) -> CardDetails:
        return CardDetails(
            cardholder_name=self.cardholder_name or "",
            card_number=self.card_number or "",
            cvv=self.cvv or "",
            expiry_month=self.expiry_month or 0,
            expiry_year=self.expiry_year or 0,
        )

    def build_payment_request(self) -> PaymentRequest:
        if not self.account_id:
            raise ValueError("account_id is required to build payment request")

        if self.payment_amount is None:
            raise ValueError("payment_amount is required to build payment request")

        return PaymentRequest(
            account_id=self.account_id,
            amount=self.payment_amount,
            payment_method=PaymentMethod(
                card=self.build_card_details(),
            ),
        )

    def mark_closed(self) -> None:
        self.step = ConversationStep.CLOSED
        self.completed = True

    def record_event(self, event: str) -> None:
        self.event_log.append(event)
