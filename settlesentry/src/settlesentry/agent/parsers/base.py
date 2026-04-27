from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from settlesentry.agent.state import ConversationState, ExtractedUserInput

ExpectedField = Literal[
    "account_id",
    "full_name",
    "dob",
    "aadhaar_last4",
    "pincode",
    "payment_amount",
    "cardholder_name",
    "card_number",
    "cvv",
    "expiry",
    "confirmation",
]


class ConversationTurn(BaseModel):
    """Small conversation record used by the parser."""

    role: Literal["user", "assistant"]
    content: str


class ParserStateSummary(BaseModel):
    """
    Privacy-aware state summary for parsing.

    Do not include full card numbers, CVV, DOB, Aadhaar, or pincode here.
    """

    step: str
    account_id: str | None = None
    account_loaded: bool = False
    verified: bool = False
    payment_amount: str | None = None
    card_last4: str | None = None
    payment_confirmed: bool = False

    @classmethod
    def from_state(cls, state: ConversationState) -> "ParserStateSummary":
        return cls(
            step=state.step.value,
            account_id=state.account_id,
            account_loaded=state.has_account_loaded(),
            verified=state.verified,
            payment_amount=str(state.payment_amount) if state.payment_amount is not None else None,
            card_last4=state.card_last4(),
            payment_confirmed=state.payment_confirmed,
        )


class ParserContext(BaseModel):
    """Context passed from the conversation controller into a parser."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    current_step: str
    expected_fields: tuple[ExpectedField, ...] = ()
    last_assistant_message: str | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()
    state_summary: ParserStateSummary

    @classmethod
    def from_state(
        cls,
        state: ConversationState,
        *,
        expected_fields: Sequence[ExpectedField] = (),
        last_assistant_message: str | None = None,
        recent_turns: Sequence[ConversationTurn] = (),
    ) -> "ParserContext":
        return cls(
            current_step=state.step.value,
            expected_fields=tuple(expected_fields),
            last_assistant_message=last_assistant_message,
            recent_turns=tuple(recent_turns),
            state_summary=ParserStateSummary.from_state(state),
        )


class InputParser(Protocol):
    """Common parser interface used by the agent service."""

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput: ...
