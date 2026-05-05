from dataclasses import dataclass
from enum import StrEnum


class AgentMode(StrEnum):
    DETERMINISTIC_WORKFLOW = "deterministic-workflow"
    LLM_PARSER_WORKFLOW = "llm-parser-workflow"
    LLM_PARSER_RESPONDER_WORKFLOW = "llm-parser-responder-workflow"
    LLM_AUTONOMOUS_AGENT = "llm-autonomous-agent"


@dataclass(frozen=True)
class ModeProfile:
    mode: AgentMode
    grouped_card_collection: bool
    requires_llm: bool
    description: str


MODE_PROFILES: dict[AgentMode, ModeProfile] = {
    AgentMode.DETERMINISTIC_WORKFLOW: ModeProfile(
        mode=AgentMode.DETERMINISTIC_WORKFLOW,
        grouped_card_collection=False,
        requires_llm=False,
        description="Deterministic workflow: deterministic parser and deterministic responses.",
    ),
    AgentMode.LLM_PARSER_WORKFLOW: ModeProfile(
        mode=AgentMode.LLM_PARSER_WORKFLOW,
        grouped_card_collection=True,
        requires_llm=True,
        description="LLM parser workflow: LLM parser with deterministic responses.",
    ),
    AgentMode.LLM_PARSER_RESPONDER_WORKFLOW: ModeProfile(
        mode=AgentMode.LLM_PARSER_RESPONDER_WORKFLOW,
        grouped_card_collection=True,
        requires_llm=True,
        description="LLM parser/responder workflow: LLM parser and LLM-written responses.",
    ),
    AgentMode.LLM_AUTONOMOUS_AGENT: ModeProfile(
        mode=AgentMode.LLM_AUTONOMOUS_AGENT,
        grouped_card_collection=True,
        requires_llm=True,
        description=(
            "LLM autonomous agent workflow: LLM-led conversation and tool orchestration over policy-gated payment operations."
        ),
    ),
}


LLM_REQUIRED_MODES = frozenset(mode for mode, profile in MODE_PROFILES.items() if profile.requires_llm)


def mode_profile(mode: AgentMode) -> ModeProfile:
    return MODE_PROFILES[mode]
