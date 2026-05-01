from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from settlesentry.agent.state import SafeConversationState


class ResponseContext(BaseModel):
    status: str
    required_fields: tuple[str, ...] = ()
    facts: dict[str, Any] = Field(default_factory=dict)
    safe_state: SafeConversationState


FIELD_LABELS = {
    "account_id": "account ID",
    "full_name": "full name exactly as registered on the account",
    "dob_or_aadhaar_last4_or_pincode": (
        "one verification factor: DOB in YYYY-MM-DD format, Aadhaar last 4 digits, or pincode"
    ),
    "payment_amount": "payment amount in INR",
    "cardholder_name": "cardholder name",
    "card_number": "full card number",
    "expiry": "expiry in MM/YYYY format",
    "cvv": "CVV",
    "confirmation": "confirmation by replying yes or no",
}

# Critical compliance/safety responses bypass LLM phrasing for stable evaluator
# behavior.
DETERMINISTIC_STATUSES = {
    "greeting",
    "account_not_found",
    "account_lookup_failed",
    "identity_verification_failed",
    "verification_exhausted",
    "zero_balance",
    "amount_exceeds_balance",
    "amount_exceeds_policy_limit",
    "invalid_payment_amount",
    "partial_payment_not_allowed",
    "insufficient_balance",
    "invalid_payment_request",
    "invalid_card",
    "invalid_cvv",
    "invalid_expiry",
    "network_error",
    "timeout",
    "invalid_response",
    "unexpected_status",
    "payment_failed",
    "payment_attempts_exhausted",
    "payment_success",
    "conversation_closed",
    "cancelled",
}

LOWER_AMOUNT_PROMPT = "Please share a lower payment amount in INR."
NO_PAYMENT_PROCESSED = "No payment has been processed."
CONVERSATION_CLOSED = "This conversation is now closed."
UNSAFE_PAYMENT_CONTINUE = "I cannot safely continue this payment in the chat."
TRY_AGAIN_LATER = "Please try again later or contact support if you need help."

PAYMENT_UNAVAILABLE_MESSAGE = (
    "The payment service is currently unavailable or the request timed out. "
    f"{UNSAFE_PAYMENT_CONTINUE} {TRY_AGAIN_LATER}"
)

PAYMENT_SERVICE_FAILURE_MESSAGE = (
    f"The payment could not be processed due to a payment service issue. {UNSAFE_PAYMENT_CONTINUE} {TRY_AGAIN_LATER}"
)


def pending_question(context: ResponseContext) -> str:
    fields = context.required_fields

    if not fields:
        return "Please provide the requested detail to continue."

    card_group = [field for field in fields if field in {"cardholder_name", "card_number", "expiry"}]

    if len(card_group) > 1:
        labels = [FIELD_LABELS[field] for field in card_group]
        return f"Please share the {join_labels(labels)}."

    first = fields[0]

    if first == "confirmation":
        amount = format_amount_from_text(context.safe_state.payment_amount)
        return f"Please confirm the payment of {amount} by replying yes or no."

    label = FIELD_LABELS.get(first, "requested detail")
    return f"Please share your {label}."


def append_pending_question(answer: str, context: ResponseContext) -> str:
    question = pending_question(context)

    if not question:
        return answer

    return f"{answer} {question}"


def build_status_summary(context: ResponseContext) -> str:
    state = context.safe_state

    if state.completed:
        return "This conversation is already closed."

    if not state.account_id:
        return "We have not started verification yet."

    if state.account_id and not state.account_loaded:
        return "I have your account ID and need to look up the account."

    if state.account_loaded and not state.verified:
        return "The account has been found, and identity verification is still pending."

    if state.verified and state.payment_amount is None:
        balance = context.facts.get("balance")
        if balance is not None:
            return f"Identity is verified. Your outstanding balance is {format_amount_from_text(balance)}."
        return "Identity is verified, and payment amount is pending."

    if state.payment_amount and not state.payment_confirmed:
        return "Payment details are being collected or awaiting confirmation."

    return "The payment flow is in progress."


def format_amount(value: Decimal | None) -> str:
    if value is None:
        return "the selected amount"

    return f"INR {value:.2f}"


def format_amount_from_text(value: object) -> str:
    if value in (None, ""):
        return "the selected amount"

    try:
        return format_amount(Decimal(str(value)))
    except Exception:
        return f"INR {value}"


def join_labels(labels: list[str]) -> str:
    if not labels:
        return "requested details"

    if len(labels) == 1:
        return labels[0]

    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"

    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _ask_agent_identity(context: ResponseContext) -> str:
    return append_pending_question(
        "I'm SettleSentry, a payment assistant that helps with account verification and payment.",
        context,
    )


def _ask_agent_capability(context: ResponseContext) -> str:
    return append_pending_question(
        (
            "I'll help you verify your account, show the outstanding balance, collect payment details, "
            "ask for confirmation, and process the payment only after you confirm."
        ),
        context,
    )


def _ask_current_status(context: ResponseContext) -> str:
    return append_pending_question(build_status_summary(context), context)


def _correction_applied(context: ResponseContext) -> str:
    pending = pending_question(context)
    return f"Updated. {pending}" if pending else "Updated."


def _identity_verified(context: ResponseContext) -> str:
    balance = context.facts.get("balance")
    return (
        f"Identity verified. Your outstanding balance is {format_amount_from_text(balance)}. "
        "Please share the amount you would like to pay in INR."
    )


def _identity_verification_failed(context: ResponseContext) -> str:
    attempts_remaining = context.facts.get("attempts_remaining")
    retry_note = ""

    if attempts_remaining is not None:
        retry_note = (
            f" You have {attempts_remaining} verification attempt{'s' if attempts_remaining != 1 else ''} remaining."
        )

    return f"I could not verify those details.{retry_note} {pending_question(context)}"


def _invalid_payment_request(context: ResponseContext) -> str:
    return f"Some payment details are invalid. {pending_question(context)}"


def _payment_ready_for_confirmation(context: ResponseContext) -> str:
    amount = format_amount_from_text(context.facts.get("amount"))
    card_last4 = context.facts.get("card_last4") or "the provided card"
    return f"Payment of {amount} using card ending {card_last4} is ready. Please reply yes to confirm or no to cancel."


def _payment_success(context: ResponseContext) -> str:
    transaction_id = context.facts.get("transaction_id") or context.safe_state.transaction_id or "not available"
    amount = format_amount_from_text(context.facts.get("amount") or context.safe_state.payment_amount)
    return f"Payment of {amount} was processed successfully. Transaction ID: {transaction_id}. {CONVERSATION_CLOSED}"


def _conversation_closed(context: ResponseContext) -> str:
    transaction_id = context.facts.get("transaction_id") or context.safe_state.transaction_id

    if transaction_id:
        amount = format_amount_from_text(context.facts.get("payment_amount") or context.safe_state.payment_amount)
        return (
            f"Payment of {amount} was processed successfully. Transaction ID: {transaction_id}. {CONVERSATION_CLOSED}"
        )

    return f"{CONVERSATION_CLOSED} {NO_PAYMENT_PROCESSED}"


STATIC_MESSAGES: dict[str, str] = {
    "greeting": "Hello, I'm SettleSentry. I help with account verification and payment. Please share your account ID.",
    "correction_requested": (
        "Sure. Which detail would you like to correct: account ID, full name, verification factor, "
        "payment amount, or card details?"
    ),
    "account_not_found": "I could not find an account for that account ID. Please check it and share the correct account ID.",
    "account_lookup_failed": "I could not look up that account right now. Please re-enter your account ID.",
    "amount_exceeds_balance": f"The payment amount cannot exceed the outstanding balance. {LOWER_AMOUNT_PROMPT}",
    "amount_exceeds_policy_limit": f"The payment amount exceeds the configured policy limit. {LOWER_AMOUNT_PROMPT}",
    "invalid_payment_amount": "Payment amount must be greater than zero. Please share a valid payment amount in INR.",
    "partial_payment_not_allowed": "Partial payments are not allowed for this account. Please share the full outstanding amount.",
    "insufficient_balance": f"The payment amount exceeds the outstanding balance. {LOWER_AMOUNT_PROMPT}",
    "account_loaded": "Account found. Please share your full name exactly as registered on the account.",
    "verification_exhausted": (
        "I'm unable to verify your identity after multiple attempts, so I can't continue with payment collection "
        f"in this chat. {NO_PAYMENT_PROCESSED} {TRY_AGAIN_LATER}."
    ),
    "zero_balance": "Identity verified. There is no outstanding balance to pay on this account, so the payment flow is now closed.",
    "invalid_card": "The card number appears to be invalid. Please share the full card number again.",
    "invalid_cvv": "The CVV appears to be invalid. Please share the CVV again.",
    "invalid_expiry": "The card expiry appears to be invalid or expired. Please share the expiry in MM/YYYY format again.",
    "network_error": PAYMENT_UNAVAILABLE_MESSAGE,
    "timeout": PAYMENT_UNAVAILABLE_MESSAGE,
    "invalid_response": PAYMENT_SERVICE_FAILURE_MESSAGE,
    "unexpected_status": PAYMENT_SERVICE_FAILURE_MESSAGE,
    "payment_failed": PAYMENT_SERVICE_FAILURE_MESSAGE,
    "payment_attempts_exhausted": f"Payment could not be completed after multiple attempts. {NO_PAYMENT_PROCESSED} {CONVERSATION_CLOSED}",
    "payment_not_confirmed": "Payment has not been confirmed. Please reply yes to confirm or no to cancel.",
    "cancelled": f"Payment flow cancelled. {NO_PAYMENT_PROCESSED} {CONVERSATION_CLOSED}",
}

MESSAGE_BUILDERS: dict[str, Callable[[ResponseContext], str]] = {
    "ask_agent_identity": _ask_agent_identity,
    "ask_agent_capability": _ask_agent_capability,
    "ask_current_status": _ask_current_status,
    "ask_to_repeat": pending_question,
    "correction_applied": _correction_applied,
    "input_captured": pending_question,
    "invalid_user_input": pending_question,
    "identity_verified": _identity_verified,
    "identity_verification_failed": _identity_verification_failed,
    "missing_card_fields": pending_question,
    "invalid_payment_request": _invalid_payment_request,
    "payment_ready_for_confirmation": _payment_ready_for_confirmation,
    "payment_success": _payment_success,
    "conversation_closed": _conversation_closed,
}


def build_fallback_response(context: ResponseContext) -> str:
    static_message = STATIC_MESSAGES.get(context.status)
    if static_message is not None:
        return static_message

    builder = MESSAGE_BUILDERS.get(context.status)
    if builder is not None:
        return builder(context)

    if context.required_fields:
        return pending_question(context)

    return "Please provide the requested detail to continue."
