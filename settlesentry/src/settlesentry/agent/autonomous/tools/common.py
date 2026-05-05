from __future__ import annotations

from typing import Any

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.workflow.result import AgentToolResult

TOOL_DEFAULTS: dict[str, object] = {
    "retries": 0,
    "strict": True,
}


def tool_options(
    *,
    description: str,
    category: str,
    sensitivity: str = "low",
    timeout: float = 5.0,
    mutates_state: bool = False,
    calls_external_api: bool = False,
    terminal: bool = False,
    moves_money: bool = False,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "category": category,
        "sensitivity": sensitivity,
        "mutates_state": mutates_state,
    }

    if calls_external_api:
        metadata["calls_external_api"] = True

    if terminal:
        metadata["terminal"] = True

    if moves_money:
        metadata["moves_money"] = True

    return {
        **TOOL_DEFAULTS,
        "description": description,
        "timeout": timeout,
        "metadata": metadata,
    }


def safe_tool_result(
    deps: AgentDeps,
    *,
    ok: bool,
    status: str,
    required_fields: tuple[str, ...] = (),
    facts: dict[str, Any] | None = None,
) -> AgentToolResult:
    return AgentToolResult(
        ok=ok,
        status=status,
        required_fields=required_fields,
        facts=facts or {},
        safe_state=deps.state.safe_view(session_id=deps.session_id),
    )


def verified_balance_facts(deps: AgentDeps) -> dict[str, Any]:
    if not deps.state.verified:
        return {}

    balance = deps.state.outstanding_balance()
    if balance is None:
        return {}

    return {"balance": str(balance)}


def card_last4_facts(deps: AgentDeps) -> dict[str, Any]:
    card_last4 = deps.state.card_last4()
    return {"card_last4": card_last4} if card_last4 else {}
