from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from settlesentry.agent.deps import AgentDeps
from settlesentry.security.cards import digits_only

VERIFICATION_CLAIMS = (
    r"\bidentity\s+(?:is\s+)?verified\b",
    r"\bverified\s+your\s+identity\b",
    r"\bverification\s+(?:is\s+)?complete\b",
)

PAYMENT_SUCCESS_CLAIMS = (
    r"\bpayment\s+(?:was\s+)?(?:processed|completed|successful|succeeded)\b",
    r"\btransaction\s+(?:was\s+)?(?:processed|completed|successful)\b",
)


def _decimal_variants(value: object) -> set[str]:
    if value in (None, ""):
        return set()

    variants = {str(value)}

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return variants

    variants.add(f"{decimal_value:.2f}")

    try:
        variants.add(str(decimal_value.normalize()))
    except InvalidOperation:
        pass

    return variants


def _has_claim(message: str, patterns: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def _contains_labeled_value(
    *,
    message: str,
    label_terms: tuple[str, ...],
    value: str | None,
) -> bool:
    if not value:
        return False

    lowered = message.lower()

    if not any(label in lowered for label in label_terms):
        return False

    return re.search(rf"\b{re.escape(value)}\b", message) is not None


def audit_autonomous_message(deps: AgentDeps, message: str) -> tuple[bool, str]:
    """Audit the LLM-written final message before returning it to the user."""
    message_digits = digits_only(message)

    if deps.state.provided_dob and deps.state.provided_dob in message:
        return False, "unsafe_message_dob_leak"

    if _contains_labeled_value(
        message=message,
        label_terms=("aadhaar", "aadhar"),
        value=deps.state.provided_aadhaar_last4,
    ):
        return False, "unsafe_message_aadhaar_leak"

    if _contains_labeled_value(
        message=message,
        label_terms=("pincode", "pin code"),
        value=deps.state.provided_pincode,
    ):
        return False, "unsafe_message_pincode_leak"

    if deps.state.card_number:
        card_digits = digits_only(deps.state.card_number)
        if card_digits and card_digits in message_digits:
            return False, "unsafe_message_card_number_leak"

    if deps.state.cvv and re.search(rf"\b{re.escape(deps.state.cvv)}\b", message):
        return False, "unsafe_message_cvv_leak"

    if not deps.state.verified:
        if _has_claim(message, VERIFICATION_CLAIMS):
            return False, "unsafe_verification_claim"

        for balance_variant in _decimal_variants(deps.state.outstanding_balance()):
            if balance_variant and balance_variant in message:
                return False, "unsafe_balance_leak"

    if not deps.state.transaction_id and _has_claim(message, PAYMENT_SUCCESS_CLAIMS):
        return False, "unsafe_payment_success_claim"

    return True, "safe"


__all__ = ["audit_autonomous_message"]
