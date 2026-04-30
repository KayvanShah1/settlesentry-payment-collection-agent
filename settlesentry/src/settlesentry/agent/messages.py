from __future__ import annotations

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


def build_fallback_response(context: ResponseContext) -> str:
    status = context.status

    if status == "greeting":
        # Greeting is deterministic so every mode clearly introduces SettleSentry
        # and asks for account ID.
        return "Hello, I’m SettleSentry. I help with account verification and payment. Please share your account ID."

    if status == "ask_agent_identity":
        return append_pending_question(
            "I’m SettleSentry, a payment assistant that helps with account verification and payment.",
            context,
        )

    if status == "ask_agent_capability":
        return append_pending_question(
            (
                "I’ll help you verify your account, show the outstanding balance, collect payment details, "
                "ask for confirmation, and process the payment only after you confirm."
            ),
            context,
        )

    if status == "ask_current_status":
        return append_pending_question(build_status_summary(context), context)

    if status == "ask_to_repeat":
        return pending_question(context)

    if status == "correction_requested":
        return (
            "Sure. Which detail would you like to correct: account ID, full name, verification factor, "
            "payment amount, or card details?"
        )

    if status == "correction_applied":
        pending = pending_question(context)
        return f"Updated. {pending}" if pending else "Updated."

    if status == "account_not_found":
        # Account-not-found is user-fixable; ask for a corrected ID without
        # implying payment failure.
        return "I could not find an account for that account ID. Please check it and share the correct account ID."

    if status == "account_lookup_failed":
        return "I could not look up that account right now. Please re-enter your account ID."

    if status == "amount_exceeds_balance":
        return "The payment amount cannot exceed the outstanding balance. Please share a lower payment amount in INR."

    if status == "amount_exceeds_policy_limit":
        return "The payment amount exceeds the configured policy limit. Please share a lower payment amount in INR."

    if status == "invalid_payment_amount":
        return "Payment amount must be greater than zero. Please share a valid payment amount in INR."

    if status == "partial_payment_not_allowed":
        return "Partial payments are not allowed for this account. Please share the full outstanding amount."

    if status == "insufficient_balance":
        return "The payment amount exceeds the outstanding balance. Please share a lower payment amount in INR."

    if status in {"input_captured", "invalid_user_input"}:
        return pending_question(context)

    if status == "account_loaded":
        return "Account found. Please share your full name exactly as registered on the account."

    if status == "identity_verified":
        balance = context.facts.get("balance")
        return (
            f"Identity verified. Your outstanding balance is {format_amount_from_text(balance)}. "
            "Please share the amount you would like to pay in INR."
        )

    if status == "identity_verification_failed":
        # Failed verification tells attempts remaining but never reveals which
        # sensitive field was expected or matched.
        attempts_remaining = context.facts.get("attempts_remaining")
        retry_note = ""

        if attempts_remaining is not None:
            retry_note = f" You have {attempts_remaining} verification attempt{'s' if attempts_remaining != 1 else ''} remaining."

        return f"I could not verify those details.{retry_note} {pending_question(context)}"

    if status == "verification_exhausted":
        return (
            "I’m unable to verify your identity after multiple attempts, so I can’t continue with payment collection "
            "in this chat. No payment has been processed."
        )

    if status == "zero_balance":
        return "Identity verified. This account has no outstanding balance, so no payment is due. This conversation is now closed."

    if status == "missing_card_fields":
        return pending_question(context)

    if status == "invalid_payment_request":
        return f"Some payment details are invalid. {pending_question(context)}"

    if status == "invalid_card":
        return "The card number appears to be invalid. Please share the full card number again."

    if status == "invalid_cvv":
        return "The CVV appears to be invalid. Please share the CVV again."

    if status == "invalid_expiry":
        return "The card expiry appears to be invalid or expired. Please share the expiry in MM/YYYY format again."

    if status in {"network_error", "timeout"}:
        # Terminal service errors close safely because payment status may be
        # ambiguous after timeout/network failure.
        return (
            "The payment service is currently unavailable or the request timed out. "
            "I cannot safely continue this payment in the chat. Please try again later."
        )

    if status in {"invalid_response", "unexpected_status", "payment_failed"}:
        return (
            "The payment could not be processed due to a payment service issue. "
            "I cannot safely continue this payment in the chat."
        )

    if status == "payment_attempts_exhausted":
        return "Payment could not be completed after multiple attempts. No payment has been processed. This conversation is now closed."

    if status == "payment_ready_for_confirmation":
        amount = format_amount_from_text(context.facts.get("amount"))
        card_last4 = context.facts.get("card_last4") or "the provided card"
        return (
            f"Payment of {amount} using card ending {card_last4} is ready. Please reply yes to confirm or no to cancel."
        )

    if status == "payment_not_confirmed":
        return "Payment has not been confirmed. Please reply yes to confirm or no to cancel."

    if status == "payment_success":
        transaction_id = context.facts.get("transaction_id") or context.safe_state.transaction_id or "not available"
        amount = format_amount_from_text(context.facts.get("amount") or context.safe_state.payment_amount)
        return f"Payment of {amount} was processed successfully. Transaction ID: {transaction_id}. This conversation is now closed."

    if status == "conversation_closed":
        transaction_id = context.facts.get("transaction_id") or context.safe_state.transaction_id

        if transaction_id:
            amount = format_amount_from_text(context.facts.get("payment_amount") or context.safe_state.payment_amount)
            return f"Payment of {amount} was processed successfully. Transaction ID: {transaction_id}. This conversation is now closed."

        return "This conversation is now closed. No payment has been processed."

    if status == "cancelled":
        return "Payment flow cancelled. No payment has been processed. This conversation is now closed."

    if context.required_fields:
        return pending_question(context)

    return "Please provide the requested detail to continue."


def pending_question(context: ResponseContext) -> str:
    # Required fields are converted into user-facing prompts here; debug awkward
    # prompts by checking required_fields first.
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
