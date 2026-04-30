from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from settlesentry.agent.parsing.base import InputParser
from settlesentry.agent.parsing.factory import build_input_parser
from settlesentry.agent.response.writer import ResponseGenerator, build_response_generator
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
    responder: ResponseGenerator = field(default_factory=build_response_generator)
    session_id: str = field(default_factory=lambda: uuid4().hex[:12])

    # Explicit behavior flag.
    # False = sequential card collection.
    # True = grouped card collection, intended for LLM parser mode.
    grouped_card_collection: bool = False
