from pydantic import BaseModel, ConfigDict, Field

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import SafeConversationState
from settlesentry.agent.workflow.routing import required_fields
from settlesentry.security.redaction import redact_sensitive_text


class AutonomousMemoryTurn(BaseModel):
    role: str
    content: str


class AutonomousMemoryPayload(BaseModel):
    """LLM-facing context contract for one autonomous turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_message: str = Field(
        description="Latest raw user message. Kept unredacted so the model can extract tool arguments."
    )
    safe_state: SafeConversationState
    required_fields: tuple[str, ...]
    recent_turns: list[AutonomousMemoryTurn] = Field(default_factory=list)
    turn_instruction: str = (
        "Use available tools for actionable values in user_message. "
        "Ask a concise question when information is missing. "
        "Use tool results as the source of truth."
    )


def safe_recent_turns(
    deps: AgentDeps,
    *,
    limit: int = 8,
) -> list[AutonomousMemoryTurn]:
    """Return redacted recent conversation turns for LLM context."""
    return [
        AutonomousMemoryTurn(
            role=turn.role,
            content=redact_sensitive_text(turn.content),
        )
        for turn in deps.recent_turns(limit=limit)
    ]


def build_autonomous_memory_payload(
    deps: AgentDeps,
    user_input: str,
) -> AutonomousMemoryPayload:
    """Build the privacy-safe LLM context payload for autonomous mode.

    The latest user input remains raw because the LLM needs to extract values for
    tool arguments. Prior turns are redacted to avoid repeated exposure of secrets.
    """
    return AutonomousMemoryPayload(
        user_message=user_input,
        safe_state=deps.state.safe_view(session_id=deps.session_id),
        required_fields=required_fields(deps),
        recent_turns=safe_recent_turns(deps, limit=8),
    )
