from __future__ import annotations

from decimal import Decimal

from settlesentry.agent.agent import Agent
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


def make_agent() -> Agent:
    return Agent(
        payments_client=FakePaymentsClient(),
        parser=DeterministicInputParser(),
        responder=DeterministicResponseGenerator(),
        grouped_card_collection=False,
    )


# Public interface tests: these mirror how the assignment evaluator calls
# Agent.next().
def test_agent_next_returns_required_assignment_shape():
    agent = make_agent()

    response = agent.next("Hi")

    assert isinstance(response, dict)
    assert set(response.keys()) == {"message"}
    assert isinstance(response["message"], str)
    assert "account" in response["message"].lower()


def test_agent_keeps_state_inside_single_instance():
    agent = make_agent()

    assert agent.state.account_id is None
    assert agent.session_id


def test_agent_can_progress_from_greeting_to_account_lookup():
    agent = make_agent()

    agent.next("Hi")
    response = agent.next("ACC1001")

    assert agent.state.account_id == "ACC1001"
    assert agent.state.has_account_loaded() is True
    assert agent.state.step == ConversationStep.WAITING_FOR_FULL_NAME
    assert "full name" in response["message"].lower()


# Account IDs are opaque; unknown IDs should go to lookup and recover cleanly.
def test_agent_treats_account_id_as_opaque_and_uses_lookup_result():
    agent = make_agent()

    agent.next("Hi")
    response = agent.next("AC1001")

    message = response["message"].lower()

    assert agent.state.account_id == "AC1001"
    assert agent.state.has_account_loaded() is False
    assert agent.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID
    assert "account" in message
    assert "could not find" in message
    assert "payment could not be processed" not in message


def test_agent_reprompts_when_account_not_found():
    agent = make_agent()

    agent.next("Hi")
    response = agent.next("UNKNOWN123")

    message = response["message"].lower()

    assert agent.state.account_id == "UNKNOWN123"
    assert agent.state.has_account_loaded() is False
    assert agent.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID
    assert "account" in message
    assert "could not find" in message
    assert "payment could not be processed" not in message


def test_agent_recovers_after_account_not_found():
    agent = make_agent()

    agent.next("Hi")
    first_response = agent.next("UNKNOWN123")
    second_response = agent.next("ACC1001")

    assert "could not find" in first_response["message"].lower()
    assert agent.state.account_id == "ACC1001"
    assert agent.state.has_account_loaded() is True
    assert agent.state.step == ConversationStep.WAITING_FOR_FULL_NAME
    assert "full name" in second_response["message"].lower()


def test_agent_handles_side_question_without_losing_state():
    agent = make_agent()

    agent.next("Hi")
    response = agent.next("who are you?")

    assert agent.state.account_id is None
    assert agent.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID
    assert "settlesentry" in response["message"].lower()
    assert "account" in response["message"].lower()


def test_agent_closes_deterministically_when_already_in_payment_success():
    agent = make_agent()
    agent.state.step = ConversationStep.PAYMENT_SUCCESS
    agent.state.payment_amount = Decimal("400.00")
    agent.state.transaction_id = "txn_123"

    response = agent.next("anything")

    assert "Transaction ID: txn_123" in response["message"]
    assert "INR 400.00" in response["message"]
    assert agent.state.completed is True
    assert agent.state.step == ConversationStep.CLOSED


def test_agent_happy_path_processes_payment():
    agent = make_agent()

    agent.next("Hi")
    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("1990-05-14")
    agent.next("500")
    agent.next("Nithin Jain")
    agent.next("4532 0151 1283 0366")
    agent.next("12/2027")
    agent.next("123")
    response = agent.next("yes")

    assert agent.state.completed is True
    assert agent.state.transaction_id == "txn_123"
    assert "Transaction ID: txn_123" in response["message"]
