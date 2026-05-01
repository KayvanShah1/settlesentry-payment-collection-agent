from __future__ import annotations

from typing import Any, Callable

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.workflow.helpers import response_context
from settlesentry.agent.workflow.input import submit_user_input
from settlesentry.agent.workflow.operations import (
    confirm_payment,
    greet_user,
    lookup_account,
    prepare_payment,
    process_payment,
    recap_and_close,
    verify_identity,
)
from settlesentry.agent.workflow.result import AgentToolResult


GraphState = dict[str, Any]
Operation = Callable[[AgentDeps], AgentToolResult]


def operation_node(operation: Operation) -> Callable[[GraphState], GraphState]:
    def node(graph_state: GraphState) -> GraphState:
        return {"last_result": operation(graph_state["deps"])}

    return node


def submit_user_input_node(graph_state: GraphState) -> GraphState:
    deps: AgentDeps = graph_state["deps"]
    user_input: str = graph_state.get("user_input", "")

    return {"last_result": submit_user_input(deps, user_input)}


def confirm_payment_operation(deps: AgentDeps) -> AgentToolResult:
    return confirm_payment(deps, confirmed=True)


def response_node(graph_state: GraphState) -> GraphState:
    deps: AgentDeps = graph_state["deps"]
    node_result: AgentToolResult | None = graph_state.get("last_result")

    context = response_context(deps, node_result)
    message = deps.responder.generate(context)

    return {"final_response": message}


NODE_REGISTRY: dict[str, Callable[[GraphState], GraphState]] = {
    "submit_user_input": submit_user_input_node,
    "greet_user": operation_node(greet_user),
    "lookup_account": operation_node(lookup_account),
    "verify_identity": operation_node(verify_identity),
    "prepare_payment": operation_node(prepare_payment),
    "confirm_payment": operation_node(confirm_payment_operation),
    "process_payment": operation_node(process_payment),
    "recap_and_close": operation_node(recap_and_close),
    "respond": response_node,
}
