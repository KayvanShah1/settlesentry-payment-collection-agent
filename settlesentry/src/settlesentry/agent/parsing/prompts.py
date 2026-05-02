from __future__ import annotations

from pydantic import BaseModel

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsing.base import (
    ConversationTurn,
    ExpectedField,
    ParserContext,
    ParserStateSummary,
)

# Keep this prompt aligned with policy boundaries: extraction only, no
# verification, no balance/payment decisions.
# Account IDs are opaque. The parser must not correct typos like AC1001 -> ACC1001.
PARSER_INSTRUCTIONS_TEMPLATE = """
You are a structured input parser for a payment collection agent.

Return an ExtractedUserInput object using these allowed values:
- intent: one of [{allowed_intents}]
- proposed_action: one of [{allowed_actions}]

Your job is extraction only.

Core rules:
- Extract only values explicitly present in the latest user message or clearly implied by the last assistant question.
- Use expected_fields, current_step, and last_assistant_message to interpret short or bare replies.
- Never invent missing values.
- Never correct account IDs, names, dates, Aadhaar digits, pincodes, card numbers, expiry, or CVV.
- Treat account_id as an opaque identifier. Do not fix typos like AC1001 -> ACC1001.
- Do not verify identity.
- Do not decide whether balance can be revealed.
- Do not decide whether payment is allowed.
- Do not authorize or process payment.
- The workflow controller and policy layer decide the next action.

Expected-field priority:
- If expected_fields contains account_id, a bare identifier like ACC1001 should be extracted as account_id.
- If expected_fields contains full_name, a bare name like "Nithin Jain" should be extracted as full_name.
- If expected_fields contains dob, aadhaar_last4, or pincode:
  - YYYY-MM-DD should be extracted as dob.
  - Exactly 4 digits should be extracted as aadhaar_last4.
  - Exactly 6 digits should be extracted as pincode.
- If expected_fields contains dob_or_aadhaar_last4_or_pincode:
  - YYYY-MM-DD should be extracted as dob.
  - Exactly 4 digits should be extracted as aadhaar_last4.
  - Exactly 6 digits should be extracted as pincode.
- If expected_fields contains payment_amount, a bare numeric amount should be extracted as payment_amount.
- If expected_fields contains cardholder_name, a bare name should be extracted as cardholder_name.
- If expected_fields contains card_number, a long card-like numeric value should be extracted as card_number.
- If expected_fields contains expiry, values like 12/2027, 12-2027, or month 12 year 2027 should be extracted as expiry_month and expiry_year.
- If expected_fields contains cvv, a short numeric value should be extracted as cvv.
- If expected_fields contains confirmation, yes/confirm/proceed should set confirmation=true, and no/cancel/stop should set confirmation=false or cancel intent if the user clearly cancels.

Important disambiguation:
- Do not extract a payment amount before identity is verified unless the user explicitly says they want to pay that amount.
- Do not extract a card number, CVV, or expiry unless the message clearly provides payment card details or those fields are expected.
- Do not treat a verification factor as a payment amount.
- Do not treat a payment amount as a verification factor.
- Do not treat a full name correction as verified identity.
- Do not mark confirmation=true unless the user is explicitly confirming payment.

Correction handling:
- If the user corrects a detail, extract both the correction intent and the corrected field value.
- Correction fields may differ from expected_fields. For example, if expected_fields contains confirmation and the user says "actually amount is INR 600", extract payment_amount=600 and use intent=correct_previous_detail.
- Do not treat a correction as payment confirmation unless the user explicitly confirms payment.
- Do not process, authorize, or approve corrected payment details; only extract the corrected fields.

Field extraction:
- Extract account_id when the user provides an account identifier.
- Extract identity fields only when provided: full_name, dob, aadhaar_last4, pincode.
- Extract payment fields only when provided: payment_amount, cardholder_name, card_number, cvv, expiry_month, expiry_year.
- If the user asks who you are, use intent=ask_agent_identity.
- If the user asks what you can do, use intent=ask_agent_capability.
- If the user asks for current status, progress, balance after verification, or where they are in the flow, use intent=ask_current_status.
- If the user asks to repeat the last question, use intent=ask_to_repeat.
- If the user wants to correct/change/update a detail, use intent=correct_previous_detail and proposed_action=handle_correction.
- If the user cancels/stops/exits the payment flow, use intent=cancel and proposed_action=cancel.

Action hints:
- If account_id is extracted, proposed_action may be lookup_account.
- If identity fields are extracted, proposed_action may be verify_identity.
- If payment fields are extracted, proposed_action may be prepare_payment.
- If confirmation=true, proposed_action should be confirm_payment, not process_payment.
- Never set proposed_action=process_payment just because the user said yes.
- Never set proposed_action=process_payment from parser output unless the controller has already confirmed payment.
""".strip()


class ParserPromptPayload(BaseModel):
    # This payload gives the LLM enough state to interpret bare replies without
    # exposing sensitive account facts.
    current_step: str
    expected_fields: tuple[ExpectedField, ...]
    last_assistant_message: str | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()
    state_summary: ParserStateSummary
    latest_user_message: str

    @classmethod
    def from_context(
        cls,
        user_input: str,
        context: ParserContext,
    ) -> "ParserPromptPayload":
        return cls(
            current_step=context.current_step,
            expected_fields=context.expected_fields,
            last_assistant_message=context.last_assistant_message,
            recent_turns=context.recent_turns,
            state_summary=context.state_summary,
            latest_user_message=user_input,
        )


def build_parser_instructions() -> str:
    return PARSER_INSTRUCTIONS_TEMPLATE.format(
        allowed_intents=", ".join(intent.value for intent in UserIntent),
        allowed_actions=", ".join(action.value for action in ProposedAction),
    )


def build_parser_user_prompt(user_input: str, context: ParserContext) -> str:
    payload = ParserPromptPayload.from_context(
        user_input=user_input,
        context=context,
    )

    return payload.model_dump_json(indent=2)
