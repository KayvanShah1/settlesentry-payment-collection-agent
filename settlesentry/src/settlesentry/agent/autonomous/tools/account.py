from __future__ import annotations

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import log_tool_call, tool_options
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.input import handle_correction
from settlesentry.agent.workflow.operations import lookup_account

ACCOUNT_TOOL_INSTRUCTIONS = """
Use account tools when the user provides an account ID.

Account IDs are opaque. Do not correct, normalize, or infer missing characters.
Only say the account was found if the tool returns account_loaded.
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

    extracted = ExtractedUserInput(account_id=normalized_account_id)

    if deps.state.account_id and deps.state.account_id != normalized_account_id:
        return handle_correction(deps, extracted)

    deps.state.merge(extracted)
    return lookup_account(deps)
