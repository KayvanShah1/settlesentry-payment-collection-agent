from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from settlesentry.agent.state import SafeConversationState


class AgentToolResult(BaseModel):
    """
    Standard result returned by guarded tools.

    Tools provide facts, missing fields, safe state, and optional next-tool
    hints. The LLM writes the final user-facing response.
    """

    ok: bool
    status: str
    required_fields: tuple[str, ...] = ()
    recommended_tool: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    safe_state: SafeConversationState
