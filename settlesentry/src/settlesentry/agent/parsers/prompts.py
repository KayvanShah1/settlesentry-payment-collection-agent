from __future__ import annotations

from pydantic import BaseModel

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import (
    ConversationTurn,
    ExpectedField,
    ParserContext,
    ParserStateSummary,
)

PARSER_INSTRUCTIONS_TEMPLATE = """
You are a structured input parser for a payment collection agent.

Return an ExtractedUserInput object using these allowed values:
- intent: one of [{allowed_intents}]
- proposed_action: one of [{allowed_actions}]

Your job is extraction only.

Rules:
- Extract only values explicitly present in the latest user message or clearly implied by the last assistant question.
- Use expected_fields and last_assistant_message to interpret bare replies.
- If expected_fields contains multiple fields and the user provides values in the same order, map them carefully.
- Do not invent missing values.
- Do not verify identity.
- Do not decide whether balance can be revealed.
- Do not decide whether payment is allowed.
- Do not authorize or process payment.
- The workflow controller and policy layer will decide the actual next action.

Field extraction:
- Extract account_id only when an account ID is present.
- Extract identity fields only when provided: full_name, dob, aadhaar_last4, pincode.
- Extract payment fields only when provided: payment_amount, cardholder_name, card_number, cvv, expiry_month, expiry_year.
- If the user confirms while confirmation is expected, set confirmation=true.
- If the user cancels/stops, use intent=cancel and proposed_action=cancel.

Action hints:
- If account_id is extracted, proposed_action may be lookup_account.
- If identity fields are extracted, proposed_action may be verify_identity.
- If payment fields are extracted, proposed_action may be prepare_payment.
- If confirmation=true, proposed_action should be confirm_payment, not process_payment.
- Never set proposed_action=process_payment just because the user said yes.
""".strip()


class ParserPromptPayload(BaseModel):
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
