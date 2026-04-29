import logging
from collections.abc import Callable
from decimal import Decimal

import pytest
import settlesentry.agent.policy as policy_module
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


def assert_denied(
    decision,
    *,
    reason: PolicyReason,
    failed_rule: str,
) -> None:
    assert decision.allowed is False
    assert decision.reason == reason
    assert decision.failed_rule == failed_rule


@pytest.fixture
def verified_state() -> ConversationState:
    return make_verified_state()


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


@pytest.mark.parametrize(
    "policy",
    [PREPARE_PAYMENT_POLICY, PROCESS_PAYMENT_POLICY],
    ids=["prepare_payment", "process_payment"],
)
def test_payment_policies_require_complete_card_fields(policy, verified_state: ConversationState):
    decision = policy.evaluate(verified_state)
    assert_denied(
        decision,
        reason=PolicyReason.MISSING_CARD_FIELDS,
        failed_rule="require_complete_card_fields",
    )


@pytest.mark.parametrize(
    ("state", "rule", "reason"),
    [
        (
            ConversationState(verification_attempts=settings.agent_policy.verification_max_attempts),
            require_verification_attempts_available,
            PolicyReason.VERIFICATION_ATTEMPTS_EXHAUSTED,
        ),
        (
            ConversationState(payment_attempts=settings.agent_policy.payment_max_attempts),
            require_payment_attempts_available,
            PolicyReason.PAYMENT_ATTEMPTS_EXHAUSTED,
        ),
    ],
    ids=["verification_attempts_exhausted", "payment_attempts_exhausted"],
)
def test_attempt_limit_rules(
    state: ConversationState,
    rule: Callable[[ConversationState], object],
    reason: PolicyReason,
):
    decision = rule(state)
    assert decision.allowed is False
    assert decision.reason == reason


def test_prepare_policy_denies_partial_payment_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    verified_state: ConversationState,
):
    monkeypatch.setattr(settings.agent_policy, "allow_partial_payments", False)
    decision = PREPARE_PAYMENT_POLICY.evaluate(verified_state)
    assert_denied(
        decision,
        reason=PolicyReason.PARTIAL_PAYMENT_NOT_ALLOWED,
        failed_rule="require_partial_payment_policy",
    )


def test_prepare_policy_allows_full_balance_payment_when_partial_disabled(
    monkeypatch: pytest.MonkeyPatch,
    verified_state: ConversationState,
):
    verified_state.payment_amount = verified_state.account.balance if verified_state.account else Decimal("0")
    monkeypatch.setattr(settings.agent_policy, "allow_partial_payments", False)
    decision = PREPARE_PAYMENT_POLICY.evaluate(verified_state)
    assert_denied(
        decision,
        reason=PolicyReason.MISSING_CARD_FIELDS,
        failed_rule="require_complete_card_fields",
    )


def test_prepare_policy_rejects_extremely_large_amount_before_request_build(verified_state: ConversationState):
    verified_state.payment_amount = Decimal("10970787975385595793.09")
    decision = PREPARE_PAYMENT_POLICY.evaluate(verified_state)
    assert_denied(
        decision,
        reason=PolicyReason.AMOUNT_EXCEEDS_BALANCE,
        failed_rule="require_amount_within_balance",
    )


def test_policy_set_logs_denied_decision_with_reason(
    monkeypatch: pytest.MonkeyPatch,
    verified_state: ConversationState,
):
    emitted: list[tuple[str, dict]] = []

    def fake_info(message, *args, **kwargs):
        emitted.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(policy_module.logger, "info", fake_info)

    decision = PREPARE_PAYMENT_POLICY.evaluate(verified_state)
    assert_denied(
        decision,
        reason=PolicyReason.MISSING_CARD_FIELDS,
        failed_rule="require_complete_card_fields",
    )

    policy_logs = [extra for message, extra in emitted if message == "policy_decision"]
    assert len(policy_logs) == 1
    assert policy_logs[0]["policy_name"] == "prepare_payment"
    assert policy_logs[0]["allowed"] is False
    assert policy_logs[0]["reason"] == PolicyReason.MISSING_CARD_FIELDS.value
    assert policy_logs[0]["failed_rule"] == "require_complete_card_fields"


def test_process_payment_denies_when_not_confirmed_after_valid_request():
    state = ConversationState(
        account_id="ACC1001",
        account=make_account(),
        verified=True,
        payment_amount=Decimal("100.00"),
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        cvv="123",
        expiry_month=12,
        expiry_year=2027,
        payment_confirmed=False,
    )

    decision = PROCESS_PAYMENT_POLICY.evaluate(state)
    assert_denied(
        decision,
        reason=PolicyReason.PAYMENT_NOT_CONFIRMED,
        failed_rule="require_payment_confirmation",
    )


def test_prepare_payment_denies_when_balance_is_zero(monkeypatch: pytest.MonkeyPatch):
    state = ConversationState(
        account_id="ACC1003",
        account=make_account(balance="0"),
        verified=True,
        payment_amount=Decimal("1.00"),
    )
    monkeypatch.setattr(settings.agent_policy, "allow_zero_balance_payment", False)

    decision = PREPARE_PAYMENT_POLICY.evaluate(state)
    assert_denied(
        decision,
        reason=PolicyReason.ZERO_BALANCE,
        failed_rule="require_positive_balance",
    )


def test_policy_set_logs_allowed_decision_at_debug(verified_state: ConversationState):
    log_path = settings.log_dir / (settings.logging.file_name or f"{settings.project_name}.log")
    start_size = log_path.stat().st_size if log_path.exists() else 0

    logger = policy_module.logger
    original_logger_level = logger.level
    original_handler_levels = [handler.level for handler in logger.handlers]

    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers:
        handler.setLevel(logging.DEBUG)

    state = verified_state.model_copy(
        update={
            "cardholder_name": "Nithin Jain",
            "card_number": "4532015112830366",
            "cvv": "123",
            "expiry_month": 12,
            "expiry_year": 2027,
            "payment_confirmed": True,
        }
    )

    try:
        decision = PROCESS_PAYMENT_POLICY.evaluate(state)
        for handler in logger.handlers:
            handler.flush()
    finally:
        logger.setLevel(original_logger_level)
        for handler, level in zip(logger.handlers, original_handler_levels):
            handler.setLevel(level)

    assert log_path.exists()
    with log_path.open(encoding="utf-8") as log_file:
        log_file.seek(start_size)
        new_logs = log_file.read()

    assert decision.allowed is True
    assert decision.reason == PolicyReason.ALLOWED
    assert decision.failed_rule is None

    assert "policy_decision" in new_logs
    assert "policy_name=process_payment" in new_logs
    assert "allowed=True" in new_logs
    assert "reason=allowed" in new_logs
