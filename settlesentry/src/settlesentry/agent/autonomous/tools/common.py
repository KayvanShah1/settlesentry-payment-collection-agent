from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.workflow.result import AgentToolResult
from settlesentry.core import OperationLogContext, get_logger

tool_logger = get_logger("AgentToolCall")

P = ParamSpec("P")
R = TypeVar("R")
TOOL_DEFAULTS: dict[str, object] = {
    "retries": 3,
    "strict": True,
    "sequential": True,
    "include_return_schema": False,
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


def log_tool_call(
    *,
    tool_name: str,
    category: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = args[0] if args else None
            deps: AgentDeps | None = getattr(ctx, "deps", None)

            if deps is None:
                return func(*args, **kwargs)

            operation = OperationLogContext(operation=f"tool_call.{tool_name}")

            tool_logger.info(
                "autonomous_tool_call_started",
                extra=operation.completed_extra(
                    session_id=deps.session_id,
                    tool_name=tool_name,
                    category=category,
                    step=deps.state.step.value,
                    tool_args=dict(kwargs),
                ),
            )

            result = func(*args, **kwargs)

            tool_logger.info(
                "autonomous_tool_call_completed",
                extra=operation.completed_extra(
                    session_id=deps.session_id,
                    tool_name=tool_name,
                    category=category,
                    step=deps.state.step.value,
                    ok=getattr(result, "ok", None),
                    status=getattr(result, "status", None),
                    required_fields=",".join(getattr(result, "required_fields", ()) or ()) or None,
                    recommended_tool=getattr(result, "recommended_tool", None),
                ),
            )

            return result

        return cast(Callable[P, R], wrapper)

    return decorator
