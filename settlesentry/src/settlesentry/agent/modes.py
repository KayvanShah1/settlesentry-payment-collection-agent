from dataclasses import dataclass
from enum import StrEnum, auto


class AgentMode(StrEnum):
    DETERMINISTIC_WORKFLOW = auto()
    LLM_PARSER_WORKFLOW = auto()
    LLM_PARSER_RESPONDER_WORKFLOW = auto()
    LLM_AUTONOMOUS_AGENT = auto()


@dataclass(frozen=True)
class ModeProfile:
    mode: AgentMode
    grouped_card_collection: bool = False


MODE_PROFILES = {
    AgentMode.DETERMINISTIC_WORKFLOW: ModeProfile(mode=AgentMode.DETERMINISTIC_WORKFLOW, grouped_card_collection=False),
    AgentMode.LLM_PARSER_WORKFLOW: ModeProfile(mode=AgentMode.LLM_PARSER_WORKFLOW, grouped_card_collection=True),
    AgentMode.LLM_PARSER_RESPONDER_WORKFLOW: ModeProfile(
        mode=AgentMode.LLM_PARSER_RESPONDER_WORKFLOW, grouped_card_collection=True
    ),
    AgentMode.LLM_AUTONOMOUS_AGENT: ModeProfile(mode=AgentMode.LLM_AUTONOMOUS_AGENT, grouped_card_collection=True),
}
