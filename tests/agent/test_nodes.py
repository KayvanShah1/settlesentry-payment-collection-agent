from __future__ import annotations

from decimal import Decimal

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.messages import build_fallback_response
from settlesentry.agent.nodes import (
    confirm_payment,
    lookup_account,
    prepare_payment,
    process_payment,
    recap_and_close,
    response_context,
    submit_user_input,
    verify_identity,
)
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.responder import DeterministicResponseGenerator
from settlesentry.agent.state import ConversationStep
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)


class FakePaymentsClient:
    def lookup_account(self, account_id: str) -> LookupResult:
        if account_id != "ACC1001":
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message="No account found with the provided account ID.",
                status_code=404,
            )

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
            error_code=PaymentsAPIErrorCode.INVALID_CARD,
            message="The card number appears to be invalid.",
            status_code=422,
        )


class TimeoutPaymentsClient(FakePaymentsClient):
    def process_payment(self, payment_request) -> PaymentResult:
        return PaymentResult(
            ok=False,
            error_code=PaymentsAPIErrorCode.TIMEOUT,
            message="The payment service took too long to respond.",
            status_code=504,
        )


def make_deps(payments_client=None) -> AgentDeps:
    return AgentDeps(
        payments_client=payments_client or FakePaymentsClient(),
        parser=DeterministicInputParser(),
        responder=DeterministicResponseGenerator(),
        grouped_card_collection=False,
    )


def load_verified_account(deps: AgentDeps) -> None:
    submit_user_input(deps, "ACC1001")
    lookup_account(deps)
    submit_user_input(deps, "Nithin Jain")
    submit_user_input(deps, "1990-05-14")
    verify_identity(deps)


def collect_ready_payment(deps: AgentDeps) -> None:
    load_verified_account(deps)
    submit_user_input(deps, "500")
    submit_user_input(deps, "Nithin Jain")
    submit_user_input(deps, "4532 0151 1283 0366")
    submit_user_input(deps, "12/2027")
    submit_user_input(deps, "123")
    prepare_payment(deps)
    confirm_payment(deps, confirmed=True)


def test_node_workflow_completes_successful_payment_and_recap():
    deps = make_deps()

    result = submit_user_input(deps, "ACC1001")
    assert result.ok is True
    assert result.recommended_tool == "lookup_account"

    result = lookup_account(deps)
    assert result.ok is True
    assert result.status == "account_loaded"
    assert result.required_fields == ("full_name",)

    submit_user_input(deps, "Nithin Jain")
    submit_user_input(deps, "1990-05-14")

    result = verify_identity(deps)
    assert result.ok is True
    assert result.status == "identity_verified"
    assert deps.state.verified is True
    assert result.required_fields == ("payment_amount",)

    result = submit_user_input(deps, "500")
    assert result.required_fields == ("cardholder_name",)

    result = submit_user_input(deps, "Nithin Jain")
    assert result.required_fields == ("card_number",)

    result = submit_user_input(deps, "4532 0151 1283 0366")
    assert result.required_fields == ("expiry",)

    result = submit_user_input(deps, "12/2027")
    assert result.required_fields == ("cvv",)

    result = submit_user_input(deps, "123")
    assert result.recommended_tool == "prepare_payment"

    result = prepare_payment(deps)
    assert result.ok is True
    assert result.status == "payment_ready_for_confirmation"
    assert result.required_fields == ("confirmation",)
    assert result.facts["card_last4"] == "0366"

    result = submit_user_input(deps, "yes")
    assert result.status == "confirmation_received"
    assert result.recommended_tool == "confirm_payment"

    result = confirm_payment(deps, confirmed=True)
    assert result.ok is True
    assert result.recommended_tool == "process_payment"

    result = process_payment(deps)
    assert result.ok is True
    assert result.status == "payment_success"
    assert result.recommended_tool == "recap_and_close"
    assert result.facts["transaction_id"] == "txn_123"
    assert deps.state.step == ConversationStep.PAYMENT_SUCCESS
    assert deps.state.card_number is None
    assert deps.state.cvv is None

    result = recap_and_close(deps)
    assert result.ok is True
    assert result.status == "conversation_closed"
    assert deps.state.completed is True
    assert deps.state.step == ConversationStep.CLOSED


def test_account_not_found_reprompts_for_account_id():
    deps = make_deps()

    submit_user_input(deps, "UNKNOWN123")
    result = lookup_account(deps)

    assert result.ok is False
    assert result.status == "account_not_found"
    assert result.required_fields == ("account_id",)
    assert deps.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID
    assert deps.state.has_account_loaded() is False


def test_account_not_found_can_recover_with_valid_account():
    deps = make_deps()

    submit_user_input(deps, "UNKNOWN123")
    lookup_account(deps)

    result = submit_user_input(deps, "ACC1001")
    assert result.recommended_tool == "lookup_account"

    result = lookup_account(deps)
    assert result.ok is True
    assert result.status == "account_loaded"
    assert deps.state.account_id == "ACC1001"
    assert deps.state.has_account_loaded() is True


def test_sequential_card_details_are_not_asked_again():
    deps = make_deps()

    load_verified_account(deps)
    submit_user_input(deps, "500")

    result = submit_user_input(deps, "Nithin Jain")
    assert deps.state.cardholder_name == "Nithin Jain"
    assert result.required_fields == ("card_number",)

    result = submit_user_input(deps, "4532 0151 1283 0366")
    assert deps.state.card_number == "4532015112830366"
    assert result.required_fields == ("expiry",)

    result = submit_user_input(deps, "12/2027")
    assert deps.state.expiry_month == 12
    assert deps.state.expiry_year == 2027
    assert result.required_fields == ("cvv",)

    result = submit_user_input(deps, "123")
    assert result.recommended_tool == "prepare_payment"


def test_side_question_does_not_reset_pending_required_field():
    deps = make_deps()

    submit_user_input(deps, "ACC1001")
    lookup_account(deps)

    result = submit_user_input(deps, "what will you do?")

    assert result.status == "ask_agent_capability"
    assert result.required_fields == ("full_name",)
    assert deps.state.step == ConversationStep.WAITING_FOR_FULL_NAME


def test_repeat_question_uses_current_required_field():
    deps = make_deps()

    submit_user_input(deps, "ACC1001")
    lookup_account(deps)

    result = submit_user_input(deps, "repeat that")
    message = build_fallback_response(response_context(deps, result))

    assert result.status == "ask_to_repeat"
    assert "full name" in message.lower()


def test_correction_with_no_field_asks_what_to_correct():
    deps = make_deps()

    result = submit_user_input(deps, "I want to correct my details")

    assert result.status == "correction_requested"


def test_correction_of_payment_amount_clears_confirmation_not_verification():
    deps = make_deps()

    collect_ready_payment(deps)

    assert deps.state.payment_confirmed is True

    result = submit_user_input(deps, "actually amount is INR 600")

    assert result.status == "correction_applied"
    assert deps.state.verified is True
    assert deps.state.payment_amount == Decimal("600")
    assert deps.state.payment_confirmed is False
    assert result.recommended_tool == "prepare_payment"


def test_correction_of_identity_resets_verification_and_payment_context():
    deps = make_deps()

    load_verified_account(deps)
    submit_user_input(deps, "500")

    assert deps.state.verified is True
    assert deps.state.payment_amount == Decimal("500")

    result = submit_user_input(deps, "actually DOB is 1990-05-14")

    assert result.status == "correction_applied"
    assert deps.state.verified is False
    assert deps.state.payment_amount is None
    assert result.recommended_tool == "verify_identity"


def test_process_payment_is_blocked_without_confirmation():
    deps = make_deps()

    load_verified_account(deps)
    submit_user_input(deps, "500")
    submit_user_input(deps, "Nithin Jain")
    submit_user_input(deps, "4532 0151 1283 0366")
    submit_user_input(deps, "12/2027")
    submit_user_input(deps, "123")

    result = process_payment(deps)

    assert result.ok is False
    assert result.status == "payment_not_confirmed"
    assert result.required_fields == ("confirmation",)
    assert deps.state.transaction_id is None


def test_failed_payment_can_retry_missing_card_fields():
    deps = make_deps(FailingPaymentsClient())

    collect_ready_payment(deps)

    result = process_payment(deps)

    assert result.ok is False
    assert result.status == "invalid_card"
    assert result.required_fields == ("card_number",)
    assert deps.state.card_number is None
    assert deps.state.completed is False


def test_terminal_payment_service_error_closes_safely():
    deps = make_deps(TimeoutPaymentsClient())

    collect_ready_payment(deps)

    result = process_payment(deps)

    assert result.ok is False
    assert result.status == "timeout"
    assert deps.state.completed is True
    assert deps.state.step == ConversationStep.CLOSED
    assert deps.state.transaction_id is None
    assert deps.state.card_number is None
    assert deps.state.cvv is None


def test_amount_exceeding_balance_is_blocked_before_card_collection():
    deps = make_deps()

    load_verified_account(deps)

    result = submit_user_input(deps, "2000")

    assert result.ok is False
    assert result.status == "amount_exceeds_balance"
    assert result.required_fields == ("payment_amount",)
    assert deps.state.payment_amount is None
    assert deps.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT


def test_corrected_amount_exceeding_balance_is_blocked_before_payment_readiness():
    deps = make_deps()

    load_verified_account(deps)
    submit_user_input(deps, "500")
    submit_user_input(deps, "Nithin Jain")
    submit_user_input(deps, "4532 0151 1283 0366")
    submit_user_input(deps, "12/2027")
    submit_user_input(deps, "123")
    prepare_payment(deps)

    result = submit_user_input(deps, "actually amount is INR 2000")

    assert result.ok is False
    assert result.status == "amount_exceeds_balance"
    assert result.required_fields == ("payment_amount",)
    assert deps.state.payment_amount is None
    assert deps.state.payment_confirmed is False
    assert deps.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
