from __future__ import annotations

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsing.base import ExpectedField
from settlesentry.agent.policy import PolicyReason
from settlesentry.agent.state import ConversationStep


def expected_fields(deps: AgentDeps) -> tuple[ExpectedField, ...]:
    # Parser guidance for bare replies like "123" or "Nithin Jain".
    step = deps.state.step

    if step in {ConversationStep.START, ConversationStep.WAITING_FOR_ACCOUNT_ID}:
        return ("account_id",)

    if step == ConversationStep.WAITING_FOR_FULL_NAME:
        return ("full_name",)

    if step == ConversationStep.WAITING_FOR_SECONDARY_FACTOR:
        return ("dob", "aadhaar_last4", "pincode")

    if step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT:
        return ("payment_amount",)

    if step in {
        ConversationStep.WAITING_FOR_CARDHOLDER_NAME,
        ConversationStep.WAITING_FOR_CARD_NUMBER,
        ConversationStep.WAITING_FOR_EXPIRY,
    }:
        if deps.grouped_card_collection:
            return tuple(
                field
                for field, missing in (
                    ("cardholder_name", not deps.state.cardholder_name),
                    ("card_number", not deps.state.card_number),
                    ("expiry", not deps.state.expiry_month or not deps.state.expiry_year),
                )
                if missing
            )

        if step == ConversationStep.WAITING_FOR_CARDHOLDER_NAME:
            return ("cardholder_name",)

        if step == ConversationStep.WAITING_FOR_CARD_NUMBER:
            return ("card_number",)

        if step == ConversationStep.WAITING_FOR_EXPIRY:
            return ("expiry",)

    if step == ConversationStep.WAITING_FOR_CVV:
        return ("cvv",)

    if step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION:
        return ("confirmation",)

    return ()


def required_fields(deps: AgentDeps) -> tuple[str, ...]:
    # User-facing missing-field resolver from current state.
    state = deps.state

    if state.completed:
        return ()

    if not state.account_id:
        return ("account_id",)

    if not state.has_account_loaded():
        return ()

    if not state.verified:
        if not state.provided_full_name:
            return ("full_name",)

        if not state.has_secondary_factor():
            return ("dob_or_aadhaar_last4_or_pincode",)

        return ()

    if state.payment_amount is None:
        return ("payment_amount",)

    if deps.grouped_card_collection:
        card_fields = tuple(
            field
            for field, missing in (
                ("cardholder_name", not state.cardholder_name),
                ("card_number", not state.card_number),
                ("expiry", not state.expiry_month or not state.expiry_year),
            )
            if missing
        )

        if card_fields:
            return card_fields

    else:
        if not state.cardholder_name:
            return ("cardholder_name",)

        if not state.card_number:
            return ("card_number",)

        if not state.expiry_month or not state.expiry_year:
            return ("expiry",)

    if not state.cvv:
        return ("cvv",)

    if not state.payment_confirmed:
        return ("confirmation",)

    return ()


def recommended_node(deps: AgentDeps) -> str | None:
    # Auto-advance only when the next operation is fully ready.
    state = deps.state

    if state.step in {ConversationStep.START, ConversationStep.WAITING_FOR_ACCOUNT_ID} and not state.account_id:
        return "greet_user"

    if state.account_id and not state.has_account_loaded():
        return "lookup_account"

    if state.has_account_loaded() and not state.verified and state.provided_full_name and state.has_secondary_factor():
        return "verify_identity"

    if state.verified and state.payment_amount is not None and state.has_complete_card_fields():
        return "process_payment" if state.payment_confirmed else "prepare_payment"

    return None


def required_fields_for_policy_reason(
    deps: AgentDeps,
    reason: PolicyReason,
) -> tuple[str, ...]:
    # Map policy reasons to the next user prompt fields.
    if reason == PolicyReason.MISSING_ACCOUNT_ID:
        return ("account_id",)

    if reason == PolicyReason.MISSING_FULL_NAME:
        return ("full_name",)

    if reason in {PolicyReason.MISSING_SECONDARY_FACTOR, PolicyReason.IDENTITY_NOT_VERIFIED}:
        return ("dob_or_aadhaar_last4_or_pincode",)

    if reason in {
        PolicyReason.MISSING_PAYMENT_AMOUNT,
        PolicyReason.INVALID_PAYMENT_AMOUNT,
        PolicyReason.AMOUNT_EXCEEDS_BALANCE,
        PolicyReason.AMOUNT_EXCEEDS_POLICY_LIMIT,
        PolicyReason.PARTIAL_PAYMENT_NOT_ALLOWED,
    }:
        return ("payment_amount",)

    if reason == PolicyReason.MISSING_CARD_FIELDS:
        return missing_card_fields(deps)

    if reason == PolicyReason.INVALID_PAYMENT_REQUEST:
        return missing_card_fields(deps) or ("card_number", "cvv", "expiry")

    if reason == PolicyReason.PAYMENT_NOT_CONFIRMED:
        return ("confirmation",)

    return ()


def missing_card_fields(deps: AgentDeps) -> tuple[str, ...]:
    state = deps.state

    if deps.grouped_card_collection:
        missing: list[str] = []

        if not state.cardholder_name:
            missing.append("cardholder_name")

        if not state.card_number:
            missing.append("card_number")

        if not state.expiry_month or not state.expiry_year:
            missing.append("expiry")

        if not state.cvv:
            missing.append("cvv")

        return tuple(missing)

    if not state.cardholder_name:
        return ("cardholder_name",)

    if not state.card_number:
        return ("card_number",)

    if not state.expiry_month or not state.expiry_year:
        return ("expiry",)

    if not state.cvv:
        return ("cvv",)

    return ()


def set_step_from_required_fields(deps: AgentDeps, fields: tuple[str, ...]) -> None:
    # Step tracks the first required field for next-turn parser context.
    if not fields:
        return

    first = fields[0]

    step_by_field = {
        "account_id": ConversationStep.WAITING_FOR_ACCOUNT_ID,
        "full_name": ConversationStep.WAITING_FOR_FULL_NAME,
        "dob_or_aadhaar_last4_or_pincode": ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        "payment_amount": ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        "cardholder_name": ConversationStep.WAITING_FOR_CARDHOLDER_NAME,
        "card_number": ConversationStep.WAITING_FOR_CARD_NUMBER,
        "expiry": ConversationStep.WAITING_FOR_EXPIRY,
        "cvv": ConversationStep.WAITING_FOR_CVV,
        "confirmation": ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION,
    }

    deps.state.step = step_by_field.get(first, deps.state.step)
