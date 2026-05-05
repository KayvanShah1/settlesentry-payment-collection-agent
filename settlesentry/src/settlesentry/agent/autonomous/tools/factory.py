from enum import StrEnum, auto

from pydantic_ai import CombinedToolset

from settlesentry.agent.autonomous.tools.account import account_toolset
from settlesentry.agent.autonomous.tools.identity import identity_toolset
from settlesentry.agent.autonomous.tools.lifecycle import lifecycle_toolset
from settlesentry.agent.autonomous.tools.payment import amount_toolset, card_toolset, confirmation_toolset
from settlesentry.agent.deps import AgentDeps


class ToolSurfacePhase(StrEnum):
    CLOSED = auto()
    ACCOUNT = auto()
    IDENTITY = auto()
    AMOUNT = auto()
    CARD = auto()
    CONFIRMATION = auto()


def current_phase(deps: AgentDeps) -> ToolSurfacePhase:
    state = deps.state

    if state.completed:
        return ToolSurfacePhase.CLOSED

    if not state.account_id or not state.has_account_loaded():
        return ToolSurfacePhase.ACCOUNT

    if not state.verified:
        return ToolSurfacePhase.IDENTITY

    if state.payment_amount is None:
        return ToolSurfacePhase.AMOUNT

    if not state.has_complete_card_fields():
        return ToolSurfacePhase.CARD

    return ToolSurfacePhase.CONFIRMATION


def all_toolsets() -> CombinedToolset:
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
    phase_tools = {
        ToolSurfacePhase.CLOSED: (),
        ToolSurfacePhase.ACCOUNT: (account_toolset,),
        ToolSurfacePhase.IDENTITY: (identity_toolset,),
        ToolSurfacePhase.AMOUNT: (amount_toolset,),
        ToolSurfacePhase.CARD: (card_toolset,),
        ToolSurfacePhase.CONFIRMATION: (card_toolset, confirmation_toolset),
    }

    return CombinedToolset([lifecycle_toolset, *phase_tools[current_phase(deps)]])
