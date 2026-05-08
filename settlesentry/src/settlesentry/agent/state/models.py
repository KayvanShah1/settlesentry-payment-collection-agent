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
)
from settlesentry.security.cards import digits_only
from settlesentry.security.identity import (
    normalize_optional_identity_text,
    validate_fixed_digits,
    validate_iso_date,
)

EXTRACTED_TO_STATE_FIELD_MAP = {
    # Parser output uses user-facing field names; state stores workflow-specific names.
    # This map is the only place they should be coupled.
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
    # Any payment-detail change invalidates prior confirmation so corrected details
    # always require reconfirmation.
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
    # Secondary factors are intentionally interchangeable: any one exact match with
    # the loaded account verifies the second factor.
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


class SafeConversationState(BaseModel):
    """
    Restricted state view exposed to tools, LLM responses, and debug views.

    Never include DOB, Aadhaar, pincode, full card number, CVV, raw account
    details, last_error, or event_log here.

    account_id is included for workflow continuity but is treated as sensitive
    in logs and redacted by the logging layer.
    """
    # Safe state is the only state allowed in responses/log summaries. Do not add
    # sensitive values here.

    session_id: str | None = None
    step: ConversationStep
    account_id: str | None = None
    account_loaded: bool = False
    verified: bool = False
    payment_amount: str | None = None
    card_last4: str | None = None
    payment_confirmed: bool = False
    verification_attempts: int = 0
    payment_attempts: int = 0
    completed: bool = False
    transaction_id: str | None = None

    @classmethod
    def from_state(
        cls,
        state: ConversationState,
        *,
        session_id: str | None = None,
    ) -> SafeConversationState:
        return cls(
            session_id=session_id,
            step=state.step,
            account_id=state.account_id,
            account_loaded=state.has_account_loaded(),
            verified=state.verified,
            payment_amount=str(state.payment_amount) if state.payment_amount is not None else None,
            card_last4=state.card_last4(),
            payment_confirmed=state.payment_confirmed,
            verification_attempts=state.verification_attempts,
            payment_attempts=state.payment_attempts,
            completed=state.completed,
            transaction_id=state.transaction_id,
        )


class ExtractedUserInput(BaseModel):
    """
    Structured user input extracted from regex, LLM, or both.

    This schema is softer than the final payment API schemas because users can
    provide partial information across multiple turns.
    """
    # Parser output is intentionally partial. Final validation happens later in
    # policy/API schemas once enough fields are collected.

    model_config = ConfigDict(str_strip_whitespace=True)

    intent: UserIntent = UserIntent.UNKNOWN
    proposed_action: ProposedAction = ProposedAction.NONE

    account_id: str | None = None

    full_name: str | None = None
    dob: str | None = None
    aadhaar_last4: str | None = None
    pincode: str | None = None

    payment_amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

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


class ConversationState(BaseModel):
    """
    Internal mutable state for one Agent instance/session.
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
        Merge newly extracted fields without clearing known context.

        Any payment-field change invalidates an earlier confirmation.
        """
        # Merge is additive by design to support out-of-order inputs. Clearing
        # happens only in explicit correction/failure branches.

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
        return tuple(getattr(self, state_field) for state_field, _ in SECONDARY_FACTOR_FIELD_MAP)

    def has_matching_secondary_factor(self) -> bool:
        if not self.account:
            return False

        # Strict exact-match check for DOB/Aadhaar/pincode. No fuzzy matching or
        # normalization beyond validators.
        for state_field, account_field in SECONDARY_FACTOR_FIELD_MAP:
            provided_value = getattr(self, state_field)

            if provided_value and provided_value == getattr(self.account, account_field):
                return True

        return False

    def has_complete_card_fields(self) -> bool:
        return all(getattr(self, field_name) for field_name in REQUIRED_CARD_FIELD_NAMES)

    def outstanding_balance(self) -> Decimal | None:
        return self.account.balance if self.account else None

    def card_last4(self) -> str | None:
        if not self.card_number:
            return None

        digits = digits_only(self.card_number)
        return digits[-4:] if len(digits) >= 4 else None

    def safe_view(self, *, session_id: str | None = None) -> SafeConversationState:
        return SafeConversationState.from_state(self, session_id=session_id)

    def build_card_details(self) -> CardDetails:
        # This can raise validation errors for invalid local card payloads before
        # calling the payment API.
        return CardDetails(
            cardholder_name=self.cardholder_name or "",
            card_number=self.card_number or "",
            cvv=self.cvv or "",
            expiry_month=self.expiry_month or 0,
            expiry_year=self.expiry_year or 0,
        )

    def build_payment_request(self) -> PaymentRequest:
        # Final payload boundary before process-payment. Keep this strict because
        # API calls should receive validated payloads only.
        if not self.account_id:
            raise ValueError("account_id is required to build payment request")

        if self.payment_amount is None:
            raise ValueError("payment_amount is required to build payment request")

        return PaymentRequest(
            account_id=self.account_id,
            amount=self.payment_amount,
            payment_method=PaymentMethod(card=self.build_card_details()),
        )

    def mark_closed(self) -> None:
        self.step = ConversationStep.CLOSED
        self.completed = True

    def record_event(self, event: str) -> None:
        self.event_log.append(event)
