from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from settlesentry.agent.parser import InputParser, build_input_parser
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
    session_id: str = field(default_factory=lambda: uuid4().hex[:12])
