from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from settlesentry.agent.autonomous.runtime import AutonomousAgentRuntime
from settlesentry.agent.autonomous.safety import audit_autonomous_message
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.response.messages import ResponseContext, build_fallback_response
from settlesentry.agent.workflow.routing import required_fields
from settlesentry.core import OperationLogContext, get_logger

logger = get_logger("AutonomousGraph")


class AutonomousGraphState(TypedDict, total=False):
    deps: AgentDeps
    user_input: str
    last_result: object | None
    final_response: str
    error_status: str | None
    safety_audit_status: str | None
    fallback_used: bool


def pre_turn_node(graph_state: AutonomousGraphState) -> AutonomousGraphState:
    deps = graph_state["deps"]
    user_input = graph_state.get("user_input", "")

    deps.add_user_turn(user_input)

    return {
        **graph_state,
        "final_response": "",
        "error_status": None,
        "safety_audit_status": None,
        "fallback_used": False,
    }


def autonomous_turn_node(
    graph_state: AutonomousGraphState,
    runtime: AutonomousAgentRuntime,
) -> AutonomousGraphState:
    deps = graph_state["deps"]
    user_input = graph_state.get("user_input", "")
    operation = OperationLogContext(operation="autonomous_turn")

    try:
        return {
            **graph_state,
            "final_response": runtime.run_turn(deps, user_input),
            "error_status": None,
        }

    except Exception as exc:
        logger.exception(
            "autonomous_turn_failed",
            extra=operation.completed_extra(
                session_id=deps.session_id,
                step=deps.state.step.value,
                error_type=type(exc).__name__,
            ),
        )

        return {
            **graph_state,
            "final_response": "",
            "error_status": "autonomous_turn_failed",
        }


def safety_audit_node(graph_state: AutonomousGraphState) -> AutonomousGraphState:
    deps = graph_state["deps"]
    message = graph_state.get("final_response", "")

    ok, status = audit_autonomous_message(deps, message)

    if ok:
        return {
            **graph_state,
            "safety_audit_status": status,
        }

    logger.warning(
        "autonomous_safety_audit_failed",
        extra={
            "session_id": deps.session_id,
            "step": deps.state.step.value,
            "status": status,
        },
    )

    return {
        **graph_state,
        "final_response": "",
        "error_status": status,
        "safety_audit_status": status,
    }


def fallback_response_node(graph_state: AutonomousGraphState) -> AutonomousGraphState:
    deps = graph_state["deps"]
    status = graph_state.get("error_status") or "unknown"

    context = ResponseContext(
        status=status,
        required_fields=required_fields(deps),
        facts={
            "amount": deps.state.payment_amount,
            "payment_amount": deps.state.payment_amount,
            "transaction_id": deps.state.transaction_id,
        },
        safe_state=deps.state.safe_view(session_id=deps.session_id),
    )

    return {
        **graph_state,
        "final_response": build_fallback_response(context),
        "fallback_used": True,
    }


def persist_response_node(graph_state: AutonomousGraphState) -> AutonomousGraphState:
    deps = graph_state["deps"]
    message = graph_state.get("final_response", "")

    if message:
        deps.add_assistant_turn(message)

    return graph_state


def route_after_agent_turn(graph_state: AutonomousGraphState) -> str:
    return "safety_audit" if graph_state.get("final_response") else "fallback_response"


def route_after_safety_audit(graph_state: AutonomousGraphState) -> str:
    return "persist_response" if graph_state.get("final_response") else "fallback_response"


def build_autonomous_graph(
    *,
    runtime: AutonomousAgentRuntime | None = None,
):
    autonomous_runtime = runtime or AutonomousAgentRuntime()
    builder = StateGraph(AutonomousGraphState)

    builder.add_node("pre_turn", pre_turn_node)
    builder.add_node(
        "autonomous_turn",
        lambda state: autonomous_turn_node(state, autonomous_runtime),
    )
    builder.add_node("safety_audit", safety_audit_node)
    builder.add_node("fallback_response", fallback_response_node)
    builder.add_node("persist_response", persist_response_node)

    builder.set_entry_point("pre_turn")

    builder.add_edge("pre_turn", "autonomous_turn")

    builder.add_conditional_edges(
        "autonomous_turn",
        route_after_agent_turn,
        {
            "safety_audit": "safety_audit",
            "fallback_response": "fallback_response",
        },
    )

    builder.add_conditional_edges(
        "safety_audit",
        route_after_safety_audit,
        {
            "persist_response": "persist_response",
            "fallback_response": "fallback_response",
        },
    )

    builder.add_edge("fallback_response", "persist_response")
    builder.add_edge("persist_response", END)

    return builder.compile()


__all__ = [
    "AutonomousGraphState",
    "build_autonomous_graph",
]
