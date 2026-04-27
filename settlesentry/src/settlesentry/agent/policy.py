from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, auto

from pydantic import BaseModel, ValidationError

from settlesentry.agent.state import ConversationState
from settlesentry.core import settings


class PolicyReason(StrEnum):
    ALLOWED = auto()

    CONVERSATION_CLOSED = auto()

    MISSING_ACCOUNT_ID = auto()
    ACCOUNT_ALREADY_LOADED = auto()
    ACCOUNT_NOT_LOADED = auto()

    MISSING_FULL_NAME = auto()
    MISSING_SECONDARY_FACTOR = auto()
    IDENTITY_NOT_VERIFIED = auto()
    VERIFICATION_ATTEMPTS_EXHAUSTED = auto()

    ZERO_BALANCE = auto()
    MISSING_PAYMENT_AMOUNT = auto()
    INVALID_PAYMENT_AMOUNT = auto()
    AMOUNT_EXCEEDS_BALANCE = auto()
    AMOUNT_EXCEEDS_POLICY_LIMIT = auto()

    MISSING_CARD_FIELDS = auto()
    INVALID_PAYMENT_REQUEST = auto()
    PAYMENT_NOT_CONFIRMED = auto()
    PAYMENT_ATTEMPTS_EXHAUSTED = auto()


class PolicyDecision(BaseModel):
    allowed: bool
    reason: PolicyReason
    failed_rule: str | None = None
    message: str | None = None

    @classmethod
    def allow(cls) -> PolicyDecision:
        return cls(
            allowed=True,
            reason=PolicyReason.ALLOWED,
        )

    @classmethod
    def deny(
        cls,
        reason: PolicyReason,
        message: str | None = None,
    ) -> PolicyDecision:
        return cls(
            allowed=False,
            reason=reason,
            message=message,
        )


PolicyCheck = Callable[[ConversationState], PolicyDecision]


@dataclass(frozen=True)
class PolicyRule:
    name: str
    check: PolicyCheck


@dataclass(frozen=True)
class PolicySet:
    name: str
    rules: tuple[PolicyRule, ...]

    def evaluate(self, state: ConversationState) -> PolicyDecision:
        for rule in self.rules:
            decision = rule.check(state)

            if not decision.allowed:
                return decision.model_copy(update={"failed_rule": rule.name})

        return PolicyDecision.allow()


def _deny(
    *,
    reason: PolicyReason,
    message: str,
) -> PolicyDecision:
    return PolicyDecision.deny(
        reason=reason,
        message=message,
    )


def _require_attempts_available(
    *,
    attempts: int,
    max_attempts: int,
    reason: PolicyReason,
    message: str,
) -> PolicyDecision:
    if attempts >= max_attempts:
        return _deny(reason=reason, message=message)

    return PolicyDecision.allow()


def require_conversation_open(state: ConversationState) -> PolicyDecision:
    if state.completed:
        return _deny(
            reason=PolicyReason.CONVERSATION_CLOSED,
            message="This conversation is already closed.",
        )

    return PolicyDecision.allow()


def require_account_id(state: ConversationState) -> PolicyDecision:
    if not state.account_id:
        return _deny(
            reason=PolicyReason.MISSING_ACCOUNT_ID,
            message="Account ID is required before account lookup.",
        )

    return PolicyDecision.allow()


def require_account_not_loaded(state: ConversationState) -> PolicyDecision:
    if state.has_account_loaded():
        return _deny(
            reason=PolicyReason.ACCOUNT_ALREADY_LOADED,
            message="Account is already loaded.",
        )

    return PolicyDecision.allow()


def require_account_loaded(state: ConversationState) -> PolicyDecision:
    if not state.has_account_loaded():
        return _deny(
            reason=PolicyReason.ACCOUNT_NOT_LOADED,
            message="Account lookup must succeed before this action.",
        )

    return PolicyDecision.allow()


def require_verification_attempts_available(state: ConversationState) -> PolicyDecision:
    return _require_attempts_available(
        attempts=state.verification_attempts,
        max_attempts=settings.agent_policy.verification_max_attempts,
        reason=PolicyReason.VERIFICATION_ATTEMPTS_EXHAUSTED,
        message="Verification attempts have been exhausted.",
    )


def require_full_name(state: ConversationState) -> PolicyDecision:
    if not state.provided_full_name:
        return _deny(
            reason=PolicyReason.MISSING_FULL_NAME,
            message="Full name is required for identity verification.",
        )

    return PolicyDecision.allow()


def require_secondary_factor(state: ConversationState) -> PolicyDecision:
    if not state.has_secondary_factor():
        return _deny(
            reason=PolicyReason.MISSING_SECONDARY_FACTOR,
            message="At least one secondary verification factor is required.",
        )

    return PolicyDecision.allow()


def require_verified_identity(state: ConversationState) -> PolicyDecision:
    if not state.verified:
        return _deny(
            reason=PolicyReason.IDENTITY_NOT_VERIFIED,
            message="Identity must be verified before this action.",
        )

    return PolicyDecision.allow()


def require_positive_balance(state: ConversationState) -> PolicyDecision:
    balance = state.outstanding_balance()

    if balance is None:
        return _deny(
            reason=PolicyReason.ACCOUNT_NOT_LOADED,
            message="Account balance is not available.",
        )

    if balance <= Decimal("0") and not settings.agent_policy.allow_zero_balance_payment:
        return _deny(
            reason=PolicyReason.ZERO_BALANCE,
            message="This account has no outstanding balance.",
        )

    return PolicyDecision.allow()


def require_payment_amount(state: ConversationState) -> PolicyDecision:
    if state.payment_amount is None:
        return _deny(
            reason=PolicyReason.MISSING_PAYMENT_AMOUNT,
            message="Payment amount is required.",
        )

    if state.payment_amount <= Decimal("0"):
        return _deny(
            reason=PolicyReason.INVALID_PAYMENT_AMOUNT,
            message="Payment amount must be greater than zero.",
        )

    return PolicyDecision.allow()


def require_amount_within_balance(state: ConversationState) -> PolicyDecision:
    balance = state.outstanding_balance()

    if balance is None:
        return _deny(
            reason=PolicyReason.ACCOUNT_NOT_LOADED,
            message="Account balance is not available.",
        )

    if state.payment_amount is not None and state.payment_amount > balance:
        return _deny(
            reason=PolicyReason.AMOUNT_EXCEEDS_BALANCE,
            message="Payment amount cannot exceed the outstanding balance.",
        )

    return PolicyDecision.allow()


def require_amount_within_policy_limit(state: ConversationState) -> PolicyDecision:
    max_amount = settings.agent_policy.max_payment_amount

    if max_amount is None or state.payment_amount is None:
        return PolicyDecision.allow()

    if state.payment_amount > Decimal(str(max_amount)):
        return _deny(
            reason=PolicyReason.AMOUNT_EXCEEDS_POLICY_LIMIT,
            message="Payment amount exceeds the configured policy limit.",
        )

    return PolicyDecision.allow()


def require_complete_card_fields(state: ConversationState) -> PolicyDecision:
    if not state.has_complete_card_fields():
        return _deny(
            reason=PolicyReason.MISSING_CARD_FIELDS,
            message="Cardholder name, card number, CVV, and expiry are required.",
        )

    return PolicyDecision.allow()


def require_valid_payment_request(state: ConversationState) -> PolicyDecision:
    try:
        state.build_payment_request()
    except (ValueError, ValidationError) as exc:
        return _deny(
            reason=PolicyReason.INVALID_PAYMENT_REQUEST,
            message=str(exc),
        )

    return PolicyDecision.allow()


def require_partial_payment_policy(state: ConversationState) -> PolicyDecision:
    balance = state.outstanding_balance()

    if (
        balance is not None
        and state.payment_amount is not None
        and not settings.agent_policy.allow_partial_payments
        and state.payment_amount != balance
    ):
        return _deny(
            reason=PolicyReason.PARTIAL_PAYMENT_NOT_ALLOWED,
            message="Partial payments are not allowed by policy.",
        )

    return PolicyDecision.allow()


def require_payment_confirmation(state: ConversationState) -> PolicyDecision:
    if not state.payment_confirmed:
        return _deny(
            reason=PolicyReason.PAYMENT_NOT_CONFIRMED,
            message="User must explicitly confirm before payment is processed.",
        )

    return PolicyDecision.allow()


def require_payment_attempts_available(state: ConversationState) -> PolicyDecision:
    return _require_attempts_available(
        attempts=state.payment_attempts,
        max_attempts=settings.agent_policy.payment_max_attempts,
        reason=PolicyReason.PAYMENT_ATTEMPTS_EXHAUSTED,
        message="Payment attempts have been exhausted.",
    )


def identity_matches_account(state: ConversationState) -> bool:
    """
    Strict verification rule.

    Full name must match exactly, and at least one secondary factor must match
    exactly. Do not use fuzzy matching for this workflow.
    """

    if not state.account:
        return False

    name_matches = state.provided_full_name == state.account.full_name
    secondary_matches = state.has_matching_secondary_factor()

    return name_matches and secondary_matches


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

COMMON_PAYMENT_REQUEST_RULES = (
    PolicyRule("require_payment_amount", require_payment_amount),
    PolicyRule("require_amount_within_balance", require_amount_within_balance),
    PolicyRule("require_amount_within_policy_limit", require_amount_within_policy_limit),
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


REVEAL_BALANCE_POLICY = PolicySet(
    name="reveal_balance",
    rules=COMMON_VERIFIED_ACCOUNT_RULES,
)


COLLECT_PAYMENT_POLICY = PolicySet(
    name="collect_payment",
    rules=PAYMENT_ELIGIBILITY_RULES,
)


PREPARE_PAYMENT_POLICY = PolicySet(
    name="prepare_payment",
    rules=PAYMENT_ELIGIBILITY_RULES + COMMON_PAYMENT_REQUEST_RULES,
)


PROCESS_PAYMENT_POLICY = PolicySet(
    name="process_payment",
    rules=PAYMENT_ELIGIBILITY_RULES
    + (PolicyRule("require_payment_attempts_available", require_payment_attempts_available),)
    + COMMON_PAYMENT_REQUEST_RULES
    + (PolicyRule("require_payment_confirmation", require_payment_confirmation),),
)
