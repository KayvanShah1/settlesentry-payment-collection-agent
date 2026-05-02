from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from settlesentry.agent.parsing.base import ConversationTurn, InputParser
from settlesentry.agent.parsing.factory import build_input_parser
from settlesentry.agent.response.writer import ResponseWriter, build_response_writer
from settlesentry.agent.state import ConversationState
from settlesentry.integrations.payments.client import PaymentsClient


@dataclass
class AgentDeps:
    """
    Runtime dependencies for one Agent/session.
    """

    state: ConversationState = field(default_factory=ConversationState)
    payments_client: PaymentsClient = field(default_factory=PaymentsClient)
    parser: InputParser = field(default_factory=build_input_parser)
    responder: ResponseWriter = field(default_factory=build_response_writer)
    session_id: str = field(default_factory=lambda: uuid4().hex[:12])

    grouped_card_collection: bool = False
    conversation_turns: list[ConversationTurn] = field(default_factory=list)

    def recent_turns(self, *, limit: int = 12) -> tuple[ConversationTurn, ...]:
        return tuple(self.conversation_turns[-limit:])

    def last_assistant_message(self) -> str | None:
        for turn in reversed(self.conversation_turns):
            if turn.role == "assistant":
                return turn.content
        return None

    def add_user_turn(self, content: str) -> None:
        self.conversation_turns.append(ConversationTurn(role="user", content=content))

    def add_assistant_turn(self, content: str) -> None:
        self.conversation_turns.append(ConversationTurn(role="assistant", content=content))
