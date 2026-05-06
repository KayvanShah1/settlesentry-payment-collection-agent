from __future__ import annotations

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import (
    log_tool_call,
    safe_tool_result,
    tool_options,
    verified_balance_facts,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.workflow.helpers import clear_payment_secrets
from settlesentry.agent.workflow.helpers import result as workflow_result
from settlesentry.agent.workflow.operations import greet_user
from settlesentry.agent.workflow.routing import required_fields
from settlesentry.core import OperationLogContext

LIFECYCLE_TOOL_INSTRUCTIONS = """
Use lifecycle tools for greetings, vague starts, safe progress checks, cancellation, or closure.

Use start_payment_flow when:
- the customer greets the assistant
- the customer asks to start payment without providing actionable details
- the flow has not started and account ID is needed

Use get_current_status when:
- the customer asks what is pending
- the customer asks where they are in the flow
- the customer asks a safe status question

Use cancel_flow when:
- the customer asks to cancel, stop, exit, decline, or end the payment flow
- the customer refuses to continue before payment processing

After cancellation or closure, do not collect more information.
""".strip()


lifecycle_toolset = FunctionToolset(
    instructions=LIFECYCLE_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@lifecycle_toolset.tool(
    name="start_payment_flow",
    **tool_options(
        description="Start the payment flow and request account ID.",
        category="lifecycle",
        timeout=3.0,
        mutates_state=True,
    ),
)
@log_tool_call(tool_name="start_payment_flow", category="lifecycle")
def start_payment_flow(ctx: RunContext[AgentDeps]) -> object:
    return greet_user(ctx.deps)


@lifecycle_toolset.tool(
    name="get_current_status",
    **tool_options(
        description="Return safe current workflow status and pending fields.",
        category="lifecycle",
        timeout=3.0,
    ),
)
@log_tool_call(tool_name="get_current_status", category="lifecycle")
def get_current_status(ctx: RunContext[AgentDeps]) -> object:
    deps = ctx.deps

    return safe_tool_result(
        deps,
        ok=True,
        status="current_status",
        required_fields=required_fields(deps),
        facts=verified_balance_facts(deps),
    )


@lifecycle_toolset.tool(
    name="cancel_flow",
    **tool_options(
        description="Cancel and close the flow without processing payment.",
        category="lifecycle",
        sensitivity="medium",
        timeout=3.0,
        mutates_state=True,
        terminal=True,
    ),
)
@log_tool_call(tool_name="cancel_flow", category="lifecycle")
def cancel_flow(ctx: RunContext[AgentDeps]) -> object:
    deps = ctx.deps
    operation = OperationLogContext(operation="cancel_flow")

    deps.state.payment_confirmed = False
    deps.state.mark_closed()
    clear_payment_secrets(deps)

    return workflow_result(
        deps,
        operation,
        ok=True,
        status="cancelled",
    )
