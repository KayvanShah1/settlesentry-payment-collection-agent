import json
from decimal import Decimal

from settlesentry.agent.parsers.base import ConversationTurn, ParserContext
from settlesentry.agent.parsers.prompts import build_parser_instructions, build_parser_user_prompt
from settlesentry.agent.state import ConversationState


def _context(
    *,
    expected_fields: tuple = (),
) -> ParserContext:
    state = ConversationState(
        account_id="ACC1001",
        card_number="4532-0151-1283-0366",
        payment_amount=Decimal("500.00"),
    )
    return ParserContext.from_state(
        state,
        expected_fields=expected_fields,
        last_assistant_message="Please share the requested fields.",
        recent_turns=(ConversationTurn(role="assistant", content="Share details"),),
    )


def test_parser_context_from_state_is_privacy_aware():
    context = _context(expected_fields=("cvv",))

    assert context.state_summary.card_last4 == "0366"
    assert context.state_summary.payment_amount == "500.00"
    dumped = context.model_dump()
    assert "card_number" not in dumped["state_summary"]
    assert "cvv" not in dumped["state_summary"]
    assert "dob" not in dumped["state_summary"]
    assert "aadhaar_last4" not in dumped["state_summary"]
    assert "pincode" not in dumped["state_summary"]


def test_parser_prompt_payload_contains_context_fields():
    context = _context(expected_fields=("dob", "aadhaar_last4"))
    prompt_json = build_parser_user_prompt("DOB is 1990-05-14", context)
    payload = json.loads(prompt_json)

    assert payload["latest_user_message"] == "DOB is 1990-05-14"
    assert payload["current_step"] == context.current_step
    assert payload["expected_fields"] == ["dob", "aadhaar_last4"]
    assert payload["state_summary"]["card_last4"] == "0366"


def test_parser_instructions_include_policy_boundaries():
    instructions = build_parser_instructions()

    assert "Your job is extraction only." in instructions
    assert "Do not verify identity." in instructions
    assert "Do not decide whether payment is allowed." in instructions
    assert "Do not authorize or process payment." in instructions
    assert "proposed_action should be confirm_payment" in instructions
    assert "Never set proposed_action=process_payment" in instructions


def test_parser_state_summary_does_not_include_sensitive_identity_or_card_values():
    state = ConversationState(
        account_id="ACC1001",
        verified=True,
        payment_amount=Decimal("500"),
        card_number="4532015112830366",
        cvv="123",
        provided_dob="1990-05-14",
        provided_aadhaar_last4="4321",
        provided_pincode="400001",
    )

    context = ParserContext.from_state(
        state,
        expected_fields=("confirmation",),
        last_assistant_message="Please confirm payment.",
    )

    summary = context.state_summary.model_dump()

    assert summary["account_id"] == "ACC1001"
    assert summary["verified"] is True
    assert summary["payment_amount"] == "500"
    assert summary["card_last4"] == "0366"

    serialized_summary = str(summary)

    assert "4532015112830366" not in serialized_summary
    assert "123" not in serialized_summary
    assert "1990-05-14" not in serialized_summary
    assert "4321" not in serialized_summary
    assert "400001" not in serialized_summary
