from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ConversationStep
from settlesentry.agent.tools.tools import (
    confirm_payment,
    lookup_account_if_allowed,
    prepare_payment_if_ready,
    process_payment_if_allowed,
    recap_and_close,
    submit_user_input,
    verify_identity_if_ready,
)
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
)


class FakePaymentsClient:
    def lookup_account(self, account_id: str) -> LookupResult:
        return LookupResult(
            ok=True,
            account=AccountDetails(
                account_id=account_id,
                full_name="Nithin Jain",
                dob="1990-05-14",
                aadhaar_last4="4321",
                pincode="400001",
                balance=Decimal("1250.75"),
            ),
            status_code=200,
        )

    def process_payment(self, payment_request) -> PaymentResult:
        return PaymentResult(
            ok=True,
            transaction_id="txn_123",
            status_code=200,
        )


class FailingPaymentsClient(FakePaymentsClient):
    def process_payment(self, payment_request) -> PaymentResult:
        return PaymentResult(
            ok=False,
            message="The card number appears to be invalid.",
            status_code=422,
        )


def run_context(deps: AgentDeps):
    return SimpleNamespace(deps=deps)


def test_tool_workflow_completes_successful_payment_and_recap():
    deps = AgentDeps(payments_client=FakePaymentsClient())
    ctx = run_context(deps)

    result = submit_user_input(ctx, "ACC1001")
    assert result.ok is True
    assert result.recommended_tool == "lookup_account_if_allowed"

    result = lookup_account_if_allowed(ctx)
    assert result.ok is True
    assert result.status == "account_loaded"
    assert result.required_fields == ("full_name",)

    result = submit_user_input(ctx, "Nithin Jain")
    assert result.ok is True
    assert result.required_fields == ("dob_or_aadhaar_last4_or_pincode",)

    result = submit_user_input(ctx, "1990-05-14")
    assert result.ok is True
    assert result.recommended_tool == "verify_identity_if_ready"

    result = verify_identity_if_ready(ctx)
    assert result.ok is True
    assert result.status == "identity_verified"
    assert deps.state.verified is True
    assert result.required_fields == ("payment_amount",)
    assert result.facts["balance"] == "1250.75"

    result = submit_user_input(ctx, "500")
    assert result.ok is True
    assert result.required_fields == ("cardholder_name",)

    result = submit_user_input(ctx, "Nithin Jain")
    assert result.ok is True
    assert result.required_fields == ("card_number",)

    result = submit_user_input(ctx, "4532 0151 1283 0366")
    assert result.ok is True
    assert result.required_fields == ("cvv",)

    result = submit_user_input(ctx, "123")
    assert result.ok is True
    assert result.required_fields == ("expiry",)

    result = submit_user_input(ctx, "12/2027")
    assert result.ok is True
    assert result.recommended_tool == "prepare_payment_if_ready"

    result = prepare_payment_if_ready(ctx)
    assert result.ok is True
    assert result.status == "payment_ready_for_confirmation"
    assert result.required_fields == ("confirmation",)
    assert result.facts["amount"] == "500"
    assert result.facts["card_last4"] == "0366"

    result = submit_user_input(ctx, "yes")
    assert result.ok is True
    assert result.status == "confirmation_received"
    assert result.recommended_tool == "confirm_payment"
    assert deps.state.payment_confirmed is False

    result = confirm_payment(ctx, confirmed=True)
    assert result.ok is True
    assert result.status == "payment_confirmed"
    assert result.recommended_tool == "process_payment_if_allowed"
    assert deps.state.payment_confirmed is True

    result = process_payment_if_allowed(ctx)
    assert result.ok is True
    assert result.status == "payment_success"
    assert result.recommended_tool == "recap_and_close"
    assert result.facts["transaction_id"] == "txn_123"
    assert deps.state.completed is False
    assert deps.state.step == ConversationStep.PAYMENT_SUCCESS
    assert deps.state.card_number is None
    assert deps.state.cvv is None

    result = recap_and_close(ctx)
    assert result.ok is True
    assert result.status == "conversation_closed"
    assert result.facts["payment_status"] == "success"
    assert result.facts["transaction_id"] == "txn_123"
    assert deps.state.completed is True
    assert deps.state.step == ConversationStep.CLOSED


def test_process_payment_is_blocked_without_confirmation():
    deps = AgentDeps(payments_client=FakePaymentsClient())
    ctx = run_context(deps)

    submit_user_input(ctx, "ACC1001")
    lookup_account_if_allowed(ctx)
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "1990-05-14")
    verify_identity_if_ready(ctx)
    submit_user_input(ctx, "500")
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "4532 0151 1283 0366")
    submit_user_input(ctx, "123")
    submit_user_input(ctx, "12/2027")

    result = process_payment_if_allowed(ctx)

    assert result.ok is False
    assert result.status == "payment_not_confirmed"
    assert result.required_fields == ("confirmation",)
    assert deps.state.completed is False
    assert deps.state.transaction_id is None


def test_verification_failure_clears_identity_inputs_and_allows_retry():
    deps = AgentDeps(payments_client=FakePaymentsClient())
    ctx = run_context(deps)

    submit_user_input(ctx, "ACC1001")
    lookup_account_if_allowed(ctx)
    submit_user_input(ctx, "Wrong Name")
    submit_user_input(ctx, "1990-05-14")

    result = verify_identity_if_ready(ctx)

    assert result.ok is False
    assert result.status == "identity_verification_failed"
    assert result.required_fields == ("full_name",)
    assert deps.state.verified is False
    assert deps.state.provided_full_name is None
    assert deps.state.provided_dob is None
    assert deps.state.completed is False


def test_safe_state_does_not_expose_sensitive_values():
    deps = AgentDeps(payments_client=FakePaymentsClient())
    ctx = run_context(deps)

    submit_user_input(ctx, "ACC1001")
    lookup_account_if_allowed(ctx)
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "1990-05-14")
    verify_identity_if_ready(ctx)
    submit_user_input(ctx, "500")
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "4532 0151 1283 0366")
    submit_user_input(ctx, "123")

    result = submit_user_input(ctx, "12/2027")
    dumped = result.safe_state.model_dump()

    assert "provided_dob" not in dumped
    assert "provided_aadhaar_last4" not in dumped
    assert "provided_pincode" not in dumped
    assert "card_number" not in dumped
    assert "cvv" not in dumped
    assert dumped["card_last4"] == "0366"


def test_failed_payment_can_retry_card_details():
    deps = AgentDeps(payments_client=FailingPaymentsClient())
    ctx = run_context(deps)

    submit_user_input(ctx, "ACC1001")
    lookup_account_if_allowed(ctx)
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "1990-05-14")
    verify_identity_if_ready(ctx)
    submit_user_input(ctx, "500")
    submit_user_input(ctx, "Nithin Jain")
    submit_user_input(ctx, "4532 0151 1283 0366")
    submit_user_input(ctx, "123")
    submit_user_input(ctx, "12/2027")
    prepare_payment_if_ready(ctx)
    confirm_payment(ctx, confirmed=True)

    result = process_payment_if_allowed(ctx)

    assert result.ok is False
    assert result.required_fields == ("card_number", "cvv", "expiry")
    assert result.facts["attempts_remaining"] >= 0
    assert deps.state.card_number is None
    assert deps.state.cvv is None
    assert deps.state.completed is False
