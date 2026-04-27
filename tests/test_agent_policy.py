from decimal import Decimal

import pytest
from pydantic import ValidationError

from settlesentry.agent.policy import (
    PREPARE_PAYMENT_POLICY,
    PROCESS_PAYMENT_POLICY,
    PolicyReason,
    identity_matches_account,
    require_payment_attempts_available,
    require_verification_attempts_available,
)
from settlesentry.agent.state import ConversationState, ExtractedUserInput
from settlesentry.core import settings
from settlesentry.integrations.payments.schemas import AccountDetails


def make_account(balance: str = "1250.75") -> AccountDetails:
    return AccountDetails(
        account_id="ACC1001",
        full_name="Nithin Jain",
        dob="1990-05-14",
        aadhaar_last4="4321",
        pincode="400001",
        balance=Decimal(balance),
    )


def make_verified_state() -> ConversationState:
    return ConversationState(
        account_id="ACC1001",
        account=make_account(),
        verified=True,
        provided_full_name="Nithin Jain",
        provided_dob="1990-05-14",
        payment_amount=Decimal("100.00"),
    )


def test_extracted_user_input_rejects_invalid_payment_amount():
    with pytest.raises(ValidationError):
        ExtractedUserInput(payment_amount="0")

    with pytest.raises(ValidationError):
        ExtractedUserInput(payment_amount="10.999")


def test_state_secondary_factor_helpers():
    state = ConversationState(provided_dob="1990-05-14")
    assert state.has_secondary_factor() is True
    assert state.has_matching_secondary_factor() is False

    state.account = make_account()
    assert state.has_matching_secondary_factor() is True


def test_identity_match_requires_exact_name_and_secondary_factor():
    state = ConversationState(
        account=make_account(),
        provided_full_name="Nithin Jain",
        provided_pincode="400001",
    )
    assert identity_matches_account(state) is True

    state.provided_full_name = "Nithin"
    assert identity_matches_account(state) is False


def test_prepare_and_process_policy_share_payment_request_guards():
    state = make_verified_state()

    prepare_decision = PREPARE_PAYMENT_POLICY.evaluate(state)
    process_decision = PROCESS_PAYMENT_POLICY.evaluate(state)

    assert prepare_decision.allowed is False
    assert prepare_decision.reason == PolicyReason.MISSING_CARD_FIELDS
    assert prepare_decision.failed_rule == "require_complete_card_fields"

    assert process_decision.allowed is False
    assert process_decision.reason == PolicyReason.MISSING_CARD_FIELDS
    assert process_decision.failed_rule == "require_complete_card_fields"


def test_verification_attempt_limit_rule():
    state = ConversationState(
        verification_attempts=settings.agent_policy.verification_max_attempts,
    )

    decision = require_verification_attempts_available(state)

    assert decision.allowed is False
    assert decision.reason == PolicyReason.VERIFICATION_ATTEMPTS_EXHAUSTED


def test_payment_attempt_limit_rule():
    state = ConversationState(
        payment_attempts=settings.agent_policy.payment_max_attempts,
    )

    decision = require_payment_attempts_available(state)

    assert decision.allowed is False
    assert decision.reason == PolicyReason.PAYMENT_ATTEMPTS_EXHAUSTED


def test_prepare_policy_denies_partial_payment_when_disabled(monkeypatch: pytest.MonkeyPatch):
    state = make_verified_state()
    state.payment_amount = Decimal("100.00")

    monkeypatch.setattr(settings.agent_policy, "allow_partial_payments", False)
    decision = PREPARE_PAYMENT_POLICY.evaluate(state)

    assert decision.allowed is False
    assert decision.reason == PolicyReason.PARTIAL_PAYMENT_NOT_ALLOWED
    assert decision.failed_rule == "require_partial_payment_policy"


def test_prepare_policy_allows_full_balance_payment_when_partial_disabled(monkeypatch: pytest.MonkeyPatch):
    state = make_verified_state()
    state.payment_amount = state.account.balance if state.account else Decimal("0")

    monkeypatch.setattr(settings.agent_policy, "allow_partial_payments", False)
    decision = PREPARE_PAYMENT_POLICY.evaluate(state)

    # Full-balance amount passes partial-payment rule and proceeds to next guard.
    assert decision.allowed is False
    assert decision.reason == PolicyReason.MISSING_CARD_FIELDS
    assert decision.failed_rule == "require_complete_card_fields"


def test_prepare_policy_rejects_extremely_large_amount_before_request_build():
    state = make_verified_state()
    state.payment_amount = Decimal("10970787975385595793.09")

    decision = PREPARE_PAYMENT_POLICY.evaluate(state)

    assert decision.allowed is False
    assert decision.reason == PolicyReason.AMOUNT_EXCEEDS_BALANCE
    assert decision.failed_rule == "require_amount_within_balance"
