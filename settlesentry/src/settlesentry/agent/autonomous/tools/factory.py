from __future__ import annotations

from pydantic_ai import CombinedToolset

from settlesentry.agent.autonomous.tools.account import account_toolset
from settlesentry.agent.autonomous.tools.identity import identity_toolset
from settlesentry.agent.autonomous.tools.lifecycle import lifecycle_toolset
from settlesentry.agent.autonomous.tools.payment import (
    amount_toolset,
    card_toolset,
    confirmation_toolset,
)
from settlesentry.agent.deps import AgentDeps


def all_toolsets() -> CombinedToolset:
    """Expose the full autonomous action surface."""
    return CombinedToolset(
        [
            lifecycle_toolset,
            account_toolset,
            identity_toolset,
            amount_toolset,
            card_toolset,
            confirmation_toolset,
        ]
    )


def available_toolsets(deps: AgentDeps) -> CombinedToolset:
    """Expose only currently relevant tool groups.

    This filters the action surface. It does not choose the next action.
    The LLM still decides which available tool to call, or whether to ask.
    """
    state = deps.state
    toolsets = [lifecycle_toolset]

    if state.completed:
        return CombinedToolset(toolsets)

    if not state.account_id or not state.has_account_loaded():
        toolsets.append(account_toolset)
        return CombinedToolset(toolsets)

    if state.has_account_loaded() and not state.verified:
        toolsets.append(identity_toolset)
        return CombinedToolset(toolsets)

    if state.verified and state.payment_amount is None:
        toolsets.append(amount_toolset)
        return CombinedToolset(toolsets)

    if state.verified and state.payment_amount is not None and not state.has_complete_card_fields():
        toolsets.append(card_toolset)
        return CombinedToolset(toolsets)

    if state.verified and state.payment_amount is not None and state.has_complete_card_fields():
        toolsets.append(card_toolset)
        toolsets.append(confirmation_toolset)
        return CombinedToolset(toolsets)

    return CombinedToolset(toolsets)
