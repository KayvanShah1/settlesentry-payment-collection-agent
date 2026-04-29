from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.nodes import (
    confirm_payment_node,
    greet_user_node,
    lookup_account_node,
    prepare_payment_node,
    process_payment_node,
    recap_and_close_node,
    response_node,
    submit_user_input_node,
    verify_identity_node,
)
from settlesentry.agent.routing import recommended_node
from settlesentry.agent.tools.models import AgentToolResult


class PaymentGraphState(TypedDict, total=False):
    deps: AgentDeps
    user_input: str
    last_result: AgentToolResult | None
    final_response: str


RESPONSE_ONLY_STATUSES = {
    "greeting",
    "account_loaded",
    "account_lookup_failed",
    "account_not_found",
    "invalid_user_input",
    "identity_verified",
    "identity_verification_failed",
    "verification_exhausted",
    "zero_balance",
    "payment_ready_for_confirmation",
    "payment_not_confirmed",
    "payment_attempts_exhausted",
    "cancelled",
    "conversation_closed",
    "ask_agent_identity",
    "ask_agent_capability",
    "ask_current_status",
    "ask_to_repeat",
    "correction_requested",
}


def route_after_node(graph_state: PaymentGraphState) -> str:
    deps = graph_state["deps"]
    result = graph_state.get("last_result")

    if deps.state.completed:
        return "respond"

    if result is not None:
        if result.status in RESPONSE_ONLY_STATUSES:
            return "respond"

        if result.recommended_tool:
            return result.recommended_tool

    node = recommended_node(deps)

    return node or "respond"


def build_payment_graph():
    builder = StateGraph(PaymentGraphState)

    builder.add_node("submit_user_input", submit_user_input_node)
    builder.add_node("greet_user", greet_user_node)
    builder.add_node("lookup_account", lookup_account_node)
    builder.add_node("verify_identity", verify_identity_node)
    builder.add_node("prepare_payment", prepare_payment_node)
    builder.add_node("confirm_payment", confirm_payment_node)
    builder.add_node("process_payment", process_payment_node)
    builder.add_node("recap_and_close", recap_and_close_node)
    builder.add_node("respond", response_node)

    route_map = {
        "greet_user": "greet_user",
        "lookup_account": "lookup_account",
        "verify_identity": "verify_identity",
        "prepare_payment": "prepare_payment",
        "confirm_payment": "confirm_payment",
        "process_payment": "process_payment",
        "recap_and_close": "recap_and_close",
        "respond": "respond",
    }

    builder.set_entry_point("submit_user_input")

    builder.add_conditional_edges("submit_user_input", route_after_node, route_map)
    builder.add_conditional_edges("greet_user", route_after_node, route_map)
    builder.add_conditional_edges("lookup_account", route_after_node, route_map)
    builder.add_conditional_edges("verify_identity", route_after_node, route_map)
    builder.add_conditional_edges("prepare_payment", route_after_node, route_map)
    builder.add_conditional_edges("confirm_payment", route_after_node, route_map)
    builder.add_conditional_edges("process_payment", route_after_node, route_map)
    builder.add_conditional_edges("recap_and_close", route_after_node, route_map)

    builder.add_edge("respond", END)

    return builder.compile()
