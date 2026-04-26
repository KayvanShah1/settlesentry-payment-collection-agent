from decimal import Decimal

import pytest
from pydantic import ValidationError

from settlesentry.agent.state import ConversationState, ConversationStep, ExtractedUserInput


def test_state_merge_updates_fields_and_resets_payment_confirmation():
    state = ConversationState(payment_confirmed=True)

    extracted = ExtractedUserInput(
        account_id="ACC1001",
        full_name="Nithin Jain",
        payment_amount=Decimal("100.00"),
        card_number="4532015112830366",
    )
    state.merge(extracted)

    assert state.account_id == "ACC1001"
    assert state.provided_full_name == "Nithin Jain"
    assert state.payment_amount == Decimal("100.00")
    assert state.card_number == "4532015112830366"
    assert state.payment_confirmed is False


def test_state_merge_applies_explicit_confirmation():
    state = ConversationState(payment_confirmed=False)
    state.merge(ExtractedUserInput(confirmation=True))
    assert state.payment_confirmed is True


def test_state_helpers_for_secondary_factor():
    state = ConversationState()
    assert state.has_secondary_factor() is False

    state.provided_aadhaar_last4 = "4321"
    assert state.has_secondary_factor() is True
    assert state.secondary_factor_values() == (None, "4321", None)


def test_has_complete_card_fields():
    state = ConversationState()
    assert state.has_complete_card_fields() is False

    state.cardholder_name = "Nithin Jain"
    state.card_number = "4532015112830366"
    state.cvv = "123"
    state.expiry_month = 12
    state.expiry_year = 2027

    assert state.has_complete_card_fields() is True


def test_card_last4_extracts_digits_only():
    state = ConversationState(card_number="4532-0151-1283-0366")
    assert state.card_last4() == "0366"


def test_build_payment_request_requires_fields():
    state = ConversationState()
    with pytest.raises(ValueError, match="account_id is required"):
        state.build_payment_request()

    state.account_id = "ACC1001"
    with pytest.raises(ValueError, match="payment_amount is required"):
        state.build_payment_request()


def test_build_payment_request_builds_valid_payload():
    state = ConversationState(
        account_id="ACC1001",
        payment_amount=Decimal("500.00"),
        cardholder_name="Nithin Jain",
        card_number="4532015112830366",
        cvv="123",
        expiry_month=12,
        expiry_year=2027,
    )

    request = state.build_payment_request()

    assert request.account_id == "ACC1001"
    assert request.amount == Decimal("500.00")
    assert request.payment_method.card.card_number == "4532015112830366"


def test_mark_closed_sets_terminal_flags():
    state = ConversationState(step=ConversationStep.START, completed=False)
    state.mark_closed()

    assert state.step == ConversationStep.CLOSED
    assert state.completed is True


def test_extracted_user_input_rejects_invalid_payment_amount():
    with pytest.raises(ValidationError):
        ExtractedUserInput(payment_amount="0")

    with pytest.raises(ValidationError):
        ExtractedUserInput(payment_amount="10.999")
