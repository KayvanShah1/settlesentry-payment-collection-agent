from __future__ import annotations

from decimal import Decimal

from pydantic_ai.exceptions import UsageLimitExceeded
from settlesentry.agent.agent import Agent
from settlesentry.agent.state import ConversationStep
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


class FakeAgentResult:
    def __init__(self, output: str):
        self.output = output

    def all_messages(self):
        return ["message-1", "message-2"]


class FakePydanticAgent:
    def __init__(self):
        self.calls = []

    def run_sync(self, user_input: str, *, deps, message_history, **kwargs):
        self.calls.append(
            {
                "user_input": user_input,
                "deps": deps,
                "message_history": message_history,
            }
        )
        return FakeAgentResult("Hello! Please share your account ID to get started.")


class FakeJSONOutputAgent(FakePydanticAgent):
    def run_sync(self, user_input: str, *, deps, message_history, **kwargs):
        self.calls.append(
            {
                "user_input": user_input,
                "deps": deps,
                "message_history": message_history,
            }
        )
        return FakeAgentResult('{"message":"Hello! Please share your account ID to get started."}')


class UsageLimitAgent:
    def run_sync(self, user_input: str, *, deps, message_history, **kwargs):
        raise UsageLimitExceeded("request limit exceeded")


class ShouldNotRunAgent:
    def run_sync(self, user_input: str, *, deps, message_history, **kwargs):
        raise AssertionError("run_sync should not be called")


def test_agent_next_returns_required_assignment_shape():
    fake_agent = FakePydanticAgent()
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=fake_agent,
    )

    response = agent.next("Hi")

    assert response == {
        "message": "Hello! Please share your account ID to get started.",
    }


def test_agent_next_unwraps_json_message_string():
    fake_agent = FakeJSONOutputAgent()
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=fake_agent,
    )

    response = agent.next("Hi")

    assert response == {
        "message": "Hello! Please share your account ID to get started.",
    }


def test_agent_preserves_message_history_between_turns():
    fake_agent = FakePydanticAgent()
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=fake_agent,
    )

    agent.next("Hi")
    agent.next("ACC1001")

    assert fake_agent.calls[0]["message_history"] == []
    assert fake_agent.calls[1]["message_history"] == ["message-1", "message-2"]


def test_agent_keeps_state_inside_single_instance():
    fake_agent = FakePydanticAgent()
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=fake_agent,
    )

    assert agent.state.account_id is None
    assert agent.session_id


def test_agent_uses_state_fallback_message_when_usage_limit_is_hit():
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=UsageLimitAgent(),
    )
    agent.state.step = ConversationStep.WAITING_FOR_FULL_NAME

    response = agent.next("1987-09-21")

    assert "full name" in response["message"].lower()
    assert agent.state.step == ConversationStep.WAITING_FOR_FULL_NAME
    assert agent.state.completed is False


def test_agent_closes_deterministically_when_already_in_payment_success():
    agent = Agent(
        payments_client=FakePaymentsClient(),
        pydantic_agent=ShouldNotRunAgent(),
    )
    agent.state.step = ConversationStep.PAYMENT_SUCCESS
    agent.state.payment_amount = Decimal("400.00")
    agent.state.transaction_id = "txn_123"

    response = agent.next("anything")

    assert "Transaction ID: txn_123" in response["message"]
    assert "INR 400.00" in response["message"]
    assert agent.state.completed is True
    assert agent.state.step == ConversationStep.CLOSED
