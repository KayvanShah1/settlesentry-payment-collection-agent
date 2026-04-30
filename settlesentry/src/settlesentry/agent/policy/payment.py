from __future__ import annotations

from settlesentry.agent.policy.models import PolicyRule, PolicySet
from settlesentry.agent.policy.rules import (
    require_account_id,
    require_account_loaded,
    require_account_not_loaded,
    require_amount_within_balance,
    require_amount_within_policy_limit,
    require_complete_card_fields,
    require_conversation_open,
    require_full_name,
    require_partial_payment_policy,
    require_payment_amount,
    require_payment_attempts_available,
    require_payment_confirmation,
    require_positive_balance,
    require_secondary_factor,
    require_valid_payment_request,
    require_verification_attempts_available,
    require_verified_identity,
)

COMMON_ACCOUNT_CONTEXT_RULES = (
    PolicyRule("require_conversation_open", require_conversation_open),
    PolicyRule("require_account_loaded", require_account_loaded),
)

COMMON_VERIFIED_ACCOUNT_RULES = COMMON_ACCOUNT_CONTEXT_RULES + (
    PolicyRule("require_verified_identity", require_verified_identity),
)

PAYMENT_ELIGIBILITY_RULES = COMMON_VERIFIED_ACCOUNT_RULES + (
    PolicyRule("require_positive_balance", require_positive_balance),
)

COMMON_PAYMENT_AMOUNT_RULES = (
    PolicyRule("require_payment_amount", require_payment_amount),
    PolicyRule("require_amount_within_balance", require_amount_within_balance),
    PolicyRule("require_partial_payment_policy", require_partial_payment_policy),
    PolicyRule("require_amount_within_policy_limit", require_amount_within_policy_limit),
)

COMMON_PAYMENT_REQUEST_RULES = COMMON_PAYMENT_AMOUNT_RULES + (
    PolicyRule("require_complete_card_fields", require_complete_card_fields),
    PolicyRule("require_valid_payment_request", require_valid_payment_request),
)

LOOKUP_ACCOUNT_POLICY = PolicySet(
    name="lookup_account",
    rules=(
        PolicyRule("require_conversation_open", require_conversation_open),
        PolicyRule("require_account_id", require_account_id),
        PolicyRule("require_account_not_loaded", require_account_not_loaded),
    ),
)

VERIFY_IDENTITY_POLICY = PolicySet(
    name="verify_identity",
    rules=COMMON_ACCOUNT_CONTEXT_RULES
    + (
        PolicyRule("require_verification_attempts_available", require_verification_attempts_available),
        PolicyRule("require_full_name", require_full_name),
        PolicyRule("require_secondary_factor", require_secondary_factor),
    ),
)

VALIDATE_PAYMENT_AMOUNT_POLICY = PolicySet(
    name="validate_payment_amount",
    rules=PAYMENT_ELIGIBILITY_RULES + COMMON_PAYMENT_AMOUNT_RULES,
)

PREPARE_PAYMENT_POLICY = PolicySet(
    name="prepare_payment",
    rules=PAYMENT_ELIGIBILITY_RULES + COMMON_PAYMENT_REQUEST_RULES,
)

PROCESS_PAYMENT_POLICY = PolicySet(
    # Payment processing has the strongest gate: verified identity, amount/card
    # validity, attempts available, and explicit confirmation.
    name="process_payment",
    rules=PAYMENT_ELIGIBILITY_RULES
    + (PolicyRule("require_payment_attempts_available", require_payment_attempts_available),)
    + COMMON_PAYMENT_REQUEST_RULES
    + (PolicyRule("require_payment_confirmation", require_payment_confirmation),),
)

__all__ = [
    "LOOKUP_ACCOUNT_POLICY",
    "VERIFY_IDENTITY_POLICY",
    "VALIDATE_PAYMENT_AMOUNT_POLICY",
    "PREPARE_PAYMENT_POLICY",
    "PROCESS_PAYMENT_POLICY",
]
