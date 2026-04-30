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
from settlesentry.agent.tools.models import AgentToolResult


class PaymentGraphState(TypedDict, total=False):
    deps: AgentDeps
    user_input: str
    last_result: AgentToolResult | None
    final_response: str


def route_after_node(graph_state: PaymentGraphState) -> str:
    """
    Route only when a node explicitly recommends the next node.

    This prevents policy-blocked states like amount_exceeds_balance from
    repeatedly re-entering workflow nodes in the same turn.
    """
    # Debug routing issues by checking result.recommended_tool from the previous
    # node.
    deps = graph_state["deps"]
    result = graph_state.get("last_result")

    if deps.state.completed:
        return "respond"

    if result is not None and result.recommended_tool:
        return result.recommended_tool

    return "respond"


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

    # Only registered node names can be returned as recommended_tool. Keep this
    # map in sync with node result values.
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

    # Every workflow node routes through the same decision function to avoid
    # hidden branch-specific behavior.
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
