from __future__ import annotations

from pydantic import BaseModel

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ConversationTurn, ExpectedField, ParserContext, ParserStateSummary


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
    allowed_intents = ", ".join(intent.value for intent in UserIntent)
    allowed_actions = ", ".join(action.value for action in ProposedAction)

    return f"""
You are a structured parser for a payment collection agent.

Return an ExtractedUserInput object using these allowed values:
- intent: one of [{allowed_intents}]
- proposed_action: one of [{allowed_actions}]

Rules:
- Extract only values explicitly present in the latest user message or clearly implied by the last assistant question.
- Use expected_fields and last_assistant_message to interpret bare replies.
- If the user gives ordered form-style values, map them to expected_fields in order.
- Do not invent missing values.
- Do not verify identity.
- Do not decide whether balance can be revealed.
- Do not decide whether payment is allowed.
- You may propose a tool-relevant action, but policy code will approve or block it.
- If the user explicitly confirms payment, set confirmation=true and proposed_action=process_payment.
- If the user cancels/stops, use intent=cancel and proposed_action=cancel.
""".strip()


def build_parser_user_prompt(user_input: str, context: ParserContext) -> str:
    payload = ParserPromptPayload.from_context(
        user_input=user_input,
        context=context,
    )

    return payload.model_dump_json(
        indent=2,
    )
