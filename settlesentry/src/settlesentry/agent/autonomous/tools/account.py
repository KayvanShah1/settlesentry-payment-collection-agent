from __future__ import annotations

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import log_tool_call, tool_options
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.helpers import clear_account_context
from settlesentry.agent.workflow.operations import lookup_account

ACCOUNT_TOOL_INSTRUCTIONS = """
Use account tools when the customer provides an account ID or corrects a previous account ID.

Account IDs are opaque:
- submit the account ID exactly as provided after trimming surrounding whitespace
- do not infer, autocorrect, normalize case, or add missing characters
- do not claim the account was found unless the tool returns account_loaded

If lookup fails with account_not_found, the next account ID provided by the customer must be submitted for a fresh lookup, not treated as a completed correction.
Changing account ID invalidates downstream identity and payment context.
""".strip()


account_toolset = FunctionToolset(
    instructions=ACCOUNT_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@account_toolset.tool(
    name="provide_account_id",
    **tool_options(
        description="Submit account ID exactly as provided and run account lookup.",
        category="account",
        sensitivity="medium",
        timeout=8.0,
        mutates_state=True,
        calls_external_api=True,
    ),
)
@log_tool_call(tool_name="provide_account_id", category="account_lookup")
def provide_account_id(
    ctx: RunContext[AgentDeps],
    account_id: str,
) -> object:
    deps = ctx.deps
    normalized_account_id = account_id.strip()

    if deps.state.account_id and deps.state.account_id != normalized_account_id:
        clear_account_context(deps)

    extracted = ExtractedUserInput(account_id=normalized_account_id)
    deps.state.merge(extracted)
    return lookup_account(deps)
