from __future__ import annotations

from decimal import Decimal

from settlesentry.agent.autonomous.tools.account import account_toolset
from settlesentry.agent.autonomous.tools.factory import (
    ToolSurfacePhase,
    available_toolsets,
    current_phase,
)
from settlesentry.agent.autonomous.tools.identity import identity_toolset
from settlesentry.agent.autonomous.tools.lifecycle import lifecycle_toolset
from settlesentry.agent.autonomous.tools.payment import (
    amount_toolset,
    card_toolset,
    final_confirmation_toolset,
    prepare_confirmation_toolset,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ConversationStep
from settlesentry.integrations.payments.schemas import AccountDetails


def toolset_members(combined) -> tuple[object, ...]:
    # PydanticAI CombinedToolset stores child toolsets differently across versions.
    # Keep this helper tolerant.
    for attr in ("toolsets", "_toolsets", "toolsets_"):
        value = getattr(combined, attr, None)
        if value is not None:
            return tuple(value)

    # Fallback: at least fail clearly if the library shape changes.
    raise AssertionError(f"Cannot inspect CombinedToolset members: {combined!r}")


def assert_available(deps: AgentDeps, expected: tuple[object, ...]) -> None:
    combined = available_toolsets(deps)
    assert toolset_members(combined) == expected


def load_account_state(deps: AgentDeps) -> None:
    deps.state.account_id = "ACC1001"
    deps.state.account = AccountDetails(
        account_id="ACC1001",
        full_name="Nithin Jain",
        dob="1990-05-14",
        aadhaar_last4="4321",
        pincode="400001",
        balance=Decimal("1250.75"),
    )


def test_account_phase_exposes_lifecycle_and_account_tools():
    deps = AgentDeps(grouped_card_collection=True)

    assert current_phase(deps) == ToolSurfacePhase.ACCOUNT
    assert_available(deps, (lifecycle_toolset, account_toolset))


def test_identity_phase_exposes_lifecycle_and_identity_tools():
    deps = AgentDeps(grouped_card_collection=True)
    load_account_state(deps)

    assert current_phase(deps) == ToolSurfacePhase.IDENTITY
    assert_available(deps, (lifecycle_toolset, identity_toolset))


def test_amount_phase_exposes_lifecycle_and_amount_tools():
    deps = AgentDeps(grouped_card_collection=True)
    load_account_state(deps)
    deps.state.verified = True

    assert current_phase(deps) == ToolSurfacePhase.AMOUNT
    assert_available(deps, (lifecycle_toolset, amount_toolset))


def test_card_phase_exposes_lifecycle_and_card_tools():
    deps = AgentDeps(grouped_card_collection=True)
    load_account_state(deps)
    deps.state.verified = True
    deps.state.payment_amount = Decimal("500.00")

    assert current_phase(deps) == ToolSurfacePhase.CARD
    assert_available(deps, (lifecycle_toolset, card_toolset))


def test_prepare_confirmation_phase_exposes_prepare_confirmation_tools():
    deps = AgentDeps(grouped_card_collection=True)
    load_account_state(deps)
    deps.state.verified = True
    deps.state.payment_amount = Decimal("500.00")
    deps.state.cardholder_name = "Nithin Jain"
    deps.state.card_number = "4532015112830366"
    deps.state.expiry_month = 12
    deps.state.expiry_year = 2027
    deps.state.cvv = "123"

    assert current_phase(deps) == ToolSurfacePhase.PREPARE_CONFIRMATION
    assert_available(deps, (lifecycle_toolset, prepare_confirmation_toolset))


def test_final_confirmation_phase_exposes_only_final_confirmation_tools():
    deps = AgentDeps(grouped_card_collection=True)
    load_account_state(deps)
    deps.state.verified = True
    deps.state.payment_amount = Decimal("500.00")
    deps.state.cardholder_name = "Nithin Jain"
    deps.state.card_number = "4532015112830366"
    deps.state.expiry_month = 12
    deps.state.expiry_year = 2027
    deps.state.cvv = "123"
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    assert current_phase(deps) == ToolSurfacePhase.FINAL_CONFIRMATION
    assert_available(deps, (lifecycle_toolset, final_confirmation_toolset))


def test_closed_phase_exposes_only_lifecycle_tools():
    deps = AgentDeps(grouped_card_collection=True)
    deps.state.mark_closed()

    assert current_phase(deps) == ToolSurfacePhase.CLOSED
    assert_available(deps, (lifecycle_toolset,))
