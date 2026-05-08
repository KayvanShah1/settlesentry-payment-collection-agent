from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from settlesentry.agent.autonomous.tools.account import provide_account_id
from settlesentry.agent.autonomous.tools.identity import provide_identity_details
from settlesentry.agent.autonomous.tools.lifecycle import (
    cancel_flow,
    get_current_status,
    start_payment_flow,
)
from settlesentry.agent.autonomous.tools.payment import (
    confirm_and_process_payment,
    correct_payment_amount,
    decline_payment,
    prepare_payment_for_confirmation,
    provide_card_details,
    provide_payment_amount,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ConversationStep
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)


@dataclass
class ToolCtx:
    deps: AgentDeps


class FakePaymentsClient:
    def __init__(self) -> None:
        self.lookup_calls: list[str] = []
        self.payment_calls: list[object] = []
        self.payment_outcomes: list[PaymentResult] = [
            PaymentResult(
                ok=True,
                transaction_id="txn_test_123",
                status_code=200,
            )
        ]

        self.accounts = {
            "ACC1001": AccountDetails(
                account_id="ACC1001",
                full_name="Nithin Jain",
                dob="1990-05-14",
                aadhaar_last4="4321",
                pincode="400001",
                balance=Decimal("1250.75"),
            ),
            "ACC1003": AccountDetails(
                account_id="ACC1003",
                full_name="Priya Agarwal",
                dob="1992-08-10",
                aadhaar_last4="2468",
                pincode="400003",
                balance=Decimal("0.00"),
            ),
        }

    def lookup_account(self, account_id: str) -> LookupResult:
        self.lookup_calls.append(account_id)

        account = self.accounts.get(account_id)
        if account is None:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message="No account found with the provided account ID.",
                status_code=404,
            )

        return LookupResult(
            ok=True,
            account=account,
            status_code=200,
        )

    def process_payment(self, payment_request) -> PaymentResult:
        self.payment_calls.append(payment_request)

        if len(self.payment_calls) <= len(self.payment_outcomes):
            return self.payment_outcomes[len(self.payment_calls) - 1]

        return self.payment_outcomes[-1]


def invalid_card_result() -> PaymentResult:
    return PaymentResult(
        ok=False,
        error_code=PaymentsAPIErrorCode.INVALID_CARD,
        message="The card number appears to be invalid.",
        status_code=422,
    )


@pytest.fixture
def payments_client() -> FakePaymentsClient:
    return FakePaymentsClient()


@pytest.fixture
def deps(payments_client: FakePaymentsClient) -> AgentDeps:
    return AgentDeps(
        payments_client=payments_client,
        grouped_card_collection=True,
    )


def ctx(deps: AgentDeps) -> ToolCtx:
    return ToolCtx(deps=deps)


def load_account(deps: AgentDeps, account_id: str = "ACC1001"):
    return provide_account_id(ctx(deps), account_id=account_id)


def verify_account(
    deps: AgentDeps,
    *,
    full_name: str = "Nithin Jain",
    dob: str = "1990-05-14",
):
    load_account(deps)
    return provide_identity_details(
        ctx(deps),
        full_name=full_name,
        dob=dob,
    )


def capture_valid_payment_amount(deps: AgentDeps, amount: Decimal = Decimal("500.00")):
    verify_account(deps)
    return provide_payment_amount(ctx(deps), amount=amount)


def capture_valid_card_details(deps: AgentDeps):
    capture_valid_payment_amount(deps)
    return provide_card_details(
        ctx(deps),
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        expiry_month=12,
        expiry_year=2027,
        cvv="123",
    )


def prepare_valid_payment(deps: AgentDeps):
    capture_valid_card_details(deps)
    return prepare_payment_for_confirmation(ctx(deps))


def test_start_payment_flow_requests_account_id(deps: AgentDeps):
    result = start_payment_flow(ctx(deps))

    assert result.ok is True
    assert result.status == "greeting"
    assert result.required_fields == ("account_id",)
    assert deps.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID


def test_get_current_status_returns_safe_pending_fields(deps: AgentDeps):
    result = get_current_status(ctx(deps))

    assert result.ok is True
    assert result.status == "current_status"
    assert result.required_fields == ("account_id",)
    assert result.safe_state.account_loaded is False
    assert "dob" not in result.facts
    assert "aadhaar_last4" not in result.facts
    assert "pincode" not in result.facts


def test_provide_account_id_loads_account(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    result = provide_account_id(ctx(deps), account_id="ACC1001")

    assert result.ok is True
    assert result.status == "account_loaded"
    assert result.required_fields == ("full_name",)
    assert deps.state.account_id == "ACC1001"
    assert deps.state.has_account_loaded() is True
    assert payments_client.lookup_calls == ["ACC1001"]


def test_provide_account_id_handles_account_not_found(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    result = provide_account_id(ctx(deps), account_id="BAD_ACCOUNT")

    assert result.ok is False
    assert result.status == "account_not_found"
    assert result.required_fields == ("account_id",)
    assert deps.state.account_id is None
    assert deps.state.has_account_loaded() is False
    assert payments_client.lookup_calls == ["BAD_ACCOUNT"]


def test_identity_verification_success_reveals_balance(deps: AgentDeps):
    load_account(deps)

    result = provide_identity_details(
        ctx(deps),
        full_name="Nithin Jain",
        dob="1990-05-14",
    )

    assert result.ok is True
    assert result.status == "identity_verified"
    assert result.required_fields == ("payment_amount",)
    assert result.facts["balance"] == "1250.75"
    assert deps.state.verified is True
    assert result.safe_state.verified is True


def test_identity_verification_failure_counts_attempt_and_does_not_reveal_balance(
    deps: AgentDeps,
):
    load_account(deps)

    result = provide_identity_details(
        ctx(deps),
        full_name="Wrong Name",
        dob="1990-05-14",
    )

    assert result.ok is False
    assert result.status == "identity_verification_failed"
    assert deps.state.verified is False
    assert deps.state.verification_attempts == 1
    assert "balance" not in result.facts
    assert result.required_fields
    assert result.safe_state.verified is False


def test_identity_verification_accepts_secondary_factor_other_than_dob(deps: AgentDeps):
    load_account(deps)

    result = provide_identity_details(
        ctx(deps),
        full_name="Nithin Jain",
        aadhaar_last4="4321",
    )

    assert result.ok is True
    assert result.status == "identity_verified"
    assert deps.state.verified is True
    assert result.facts["balance"] == "1250.75"


def test_identity_change_after_verification_invalidates_payment_context(deps: AgentDeps):
    capture_valid_payment_amount(deps)

    assert deps.state.verified is True
    assert deps.state.payment_amount == Decimal("500.00")

    result = provide_identity_details(
        ctx(deps),
        full_name="Wrong Name",
    )

    assert result.status == "correction_applied"
    assert deps.state.verified is False
    assert deps.state.payment_amount is None
    assert deps.state.payment_confirmed is False


def test_payment_amount_blocked_before_verification(deps: AgentDeps):
    result = provide_payment_amount(ctx(deps), amount=Decimal("500.00"))

    assert result.ok is False
    assert result.status == "account_not_loaded"
    assert result.required_fields == ()
    assert deps.state.payment_amount is None


def test_payment_amount_blocked_when_account_loaded_but_not_verified(deps: AgentDeps):
    load_account(deps)

    result = provide_payment_amount(ctx(deps), amount=Decimal("500.00"))

    assert result.ok is False
    assert result.status == "identity_not_verified"
    assert result.required_fields == ("dob_or_aadhaar_last4_or_pincode",)
    assert deps.state.payment_amount is None


def test_payment_amount_rejects_amount_exceeding_balance(deps: AgentDeps):
    verify_account(deps)

    result = provide_payment_amount(ctx(deps), amount=Decimal("1300.00"))

    assert result.ok is False
    assert result.status == "amount_exceeds_balance"
    assert result.required_fields == ("payment_amount",)
    assert deps.state.payment_amount is None


def test_payment_amount_accepts_partial_payment(deps: AgentDeps):
    verify_account(deps)

    result = provide_payment_amount(ctx(deps), amount=Decimal("500.00"))

    assert result.ok is True
    assert result.status == "payment_amount_captured"
    assert result.required_fields
    assert deps.state.payment_amount == Decimal("500.00")
    assert deps.state.payment_confirmed is False


def test_card_details_capture_does_not_process_payment(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    capture_valid_payment_amount(deps)

    result = provide_card_details(
        ctx(deps),
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        expiry_month=12,
        expiry_year=2027,
        cvv="123",
    )

    assert result.ok is True
    assert result.status == "card_details_captured"
    assert result.facts["card_last4"] == "0366"
    assert deps.state.has_complete_card_fields() is True
    assert deps.state.payment_confirmed is False
    assert payments_client.payment_calls == []


def test_prepare_payment_requires_complete_card_details(deps: AgentDeps):
    capture_valid_payment_amount(deps)

    result = prepare_payment_for_confirmation(ctx(deps))

    assert result.ok is False
    assert result.status == "missing_card_fields"
    assert result.required_fields


def test_prepare_payment_for_confirmation_stages_confirmation(deps: AgentDeps):
    capture_valid_card_details(deps)

    result = prepare_payment_for_confirmation(ctx(deps))

    assert result.ok is True
    assert result.status == "payment_ready_for_confirmation"
    assert result.required_fields == ("confirmation",)
    assert result.facts["amount"] == "500.00"
    assert result.facts["card_last4"] == "0366"
    assert deps.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION


def test_confirm_and_process_payment_blocked_without_prepared_payment(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    capture_valid_payment_amount(deps)

    result = confirm_and_process_payment(ctx(deps))

    assert result.ok is False
    assert result.status == "missing_card_fields"
    assert payments_client.payment_calls == []
    assert deps.state.transaction_id is None


def test_confirm_and_process_payment_success_closes_and_clears_secrets(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    prepare_valid_payment(deps)

    result = confirm_and_process_payment(ctx(deps))

    assert result.ok is True
    assert result.status == "conversation_closed"
    assert result.facts["transaction_id"] == "txn_test_123"
    assert deps.state.completed is True
    assert deps.state.transaction_id == "txn_test_123"
    assert deps.state.cardholder_name is None
    assert deps.state.card_number is None
    assert deps.state.expiry_month is None
    assert deps.state.expiry_year is None
    assert deps.state.cvv is None
    assert len(payments_client.payment_calls) == 1


def test_payment_invalid_card_clears_all_card_details_and_requests_full_card_bundle(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    payments_client.payment_outcomes = [invalid_card_result()]
    prepare_valid_payment(deps)

    result = confirm_and_process_payment(ctx(deps))

    assert result.ok is False
    assert result.status == "invalid_card"
    assert result.required_fields == (
        "cardholder_name",
        "card_number",
        "expiry",
        "cvv",
    )
    assert deps.state.cardholder_name is None
    assert deps.state.card_number is None
    assert deps.state.expiry_month is None
    assert deps.state.expiry_year is None
    assert deps.state.cvv is None
    assert deps.state.payment_confirmed is False
    assert deps.state.step == ConversationStep.WAITING_FOR_CARDHOLDER_NAME


def test_decline_payment_closes_without_processing(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    prepare_valid_payment(deps)

    result = decline_payment(ctx(deps))

    assert result.ok is True
    assert result.status == "cancelled"
    assert deps.state.completed is True
    assert deps.state.payment_confirmed is False
    assert deps.state.cardholder_name is None
    assert deps.state.card_number is None
    assert deps.state.expiry_month is None
    assert deps.state.expiry_year is None
    assert deps.state.cvv is None
    assert payments_client.payment_calls == []


def test_cancel_flow_closes_without_processing(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    capture_valid_card_details(deps)

    result = cancel_flow(ctx(deps))

    assert result.ok is True
    assert result.status == "cancelled"
    assert deps.state.completed is True
    assert deps.state.cardholder_name is None
    assert deps.state.card_number is None
    assert deps.state.expiry_month is None
    assert deps.state.expiry_year is None
    assert deps.state.cvv is None
    assert payments_client.payment_calls == []


def test_status_after_verification_may_include_balance_but_not_sensitive_fields(
    deps: AgentDeps,
):
    verify_account(deps)

    result = get_current_status(ctx(deps))

    assert result.ok is True
    assert result.status == "current_status"
    assert result.facts["balance"] == "1250.75"

    dumped = result.model_dump(mode="json")
    dumped_text = str(dumped)

    assert "1990-05-14" not in dumped_text
    assert "4321" not in dumped_text
    assert "400001" not in dumped_text
    assert "4532015112830366" not in dumped_text
    assert "123" not in dumped_text


def test_provide_account_id_retries_valid_account_after_not_found(
    deps: AgentDeps,
    payments_client: FakePaymentsClient,
):
    first = provide_account_id(ctx(deps), account_id="ACC6986")

    assert first.ok is False
    assert first.status == "account_not_found"
    assert first.required_fields == ("account_id",)
    assert deps.state.has_account_loaded() is False

    second = provide_account_id(ctx(deps), account_id="ACC1001")

    assert second.ok is True
    assert second.status == "account_loaded"
    assert second.required_fields == ("full_name",)
    assert deps.state.account_id == "ACC1001"
    assert deps.state.has_account_loaded() is True
    assert payments_client.lookup_calls == ["ACC6986", "ACC1001"]


def test_correct_payment_amount_reprepares_confirmation(deps: AgentDeps):
    prepare_valid_payment(deps)

    result = correct_payment_amount(ctx(deps), amount=Decimal("600.00"))

    assert result.ok is True
    assert result.status == "payment_ready_for_confirmation"
    assert result.required_fields == ("confirmation",)
    assert result.facts["amount"] == "600.00"
    assert deps.state.payment_amount == Decimal("600.00")
    assert deps.state.payment_confirmed is False
    assert deps.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION


def test_correct_payment_amount_blocks_amount_exceeding_balance(deps: AgentDeps):
    prepare_valid_payment(deps)

    result = correct_payment_amount(ctx(deps), amount=Decimal("2000.00"))

    assert result.ok is False
    assert result.status == "amount_exceeds_balance"
    assert result.required_fields == ("payment_amount",)
    assert deps.state.payment_amount is None
    assert deps.state.payment_confirmed is False
    assert deps.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
