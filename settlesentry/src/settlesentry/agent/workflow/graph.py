from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.workflow.nodes import NODE_REGISTRY
from settlesentry.agent.workflow.result import AgentToolResult


class PaymentGraphState(TypedDict, total=False):
    deps: AgentDeps
    user_input: str
    last_result: AgentToolResult | None
    final_response: str


ROUTE_MAP = {
    name: name
    for name in NODE_REGISTRY
    if name != "submit_user_input"
}


def route_after_node(graph_state: PaymentGraphState) -> str:
    """
    Route only when a node explicitly recommends the next node.

    This prevents policy-blocked states like amount_exceeds_balance from
    repeatedly re-entering workflow nodes in the same turn.
    """
    deps = graph_state["deps"]
    result = graph_state.get("last_result")

    if deps.state.completed:
        return "respond"

    if result is None or result.recommended_tool is None:
        return "respond"

    if result.recommended_tool not in ROUTE_MAP:
        raise ValueError(f"Invalid recommended_tool: {result.recommended_tool!r}")

    return result.recommended_tool


def build_payment_graph():
    builder = StateGraph(PaymentGraphState)

    for node_name, node_callable in NODE_REGISTRY.items():
        builder.add_node(node_name, node_callable)

    builder.set_entry_point("submit_user_input")

    # Every workflow node routes through the same decision function to avoid
    # hidden branch-specific behavior.
    for node_name in NODE_REGISTRY:
        if node_name != "respond":
            builder.add_conditional_edges(node_name, route_after_node, ROUTE_MAP)

    builder.add_edge("respond", END)

    return builder.compile()
