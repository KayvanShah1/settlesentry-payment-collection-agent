from __future__ import annotations

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import tool_options
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.input import handle_correction
from settlesentry.agent.workflow.operations import verify_identity

IDENTITY_TOOL_INSTRUCTIONS = """
Use identity tools when the user provides full name, DOB, Aadhaar last 4, or pincode.

Full name alone is not enough. One secondary factor is required.
The tool result is the only source of truth for verification.
On failure, do not reveal which field failed; ask only for required_fields.
If verification is exhausted, stop payment collection.
""".strip()


identity_toolset = FunctionToolset(
    instructions=IDENTITY_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@identity_toolset.tool(
    name="provide_identity_details",
    **tool_options(
        description="Submit identity details and run deterministic verification.",
        category="identity",
        sensitivity="high",
        mutates_state=True,
    ),
)
def provide_identity_details(
    ctx: RunContext[AgentDeps],
    full_name: str | None = None,
    dob: str | None = None,
    aadhaar_last4: str | None = None,
    pincode: str | None = None,
) -> object:
    deps = ctx.deps

    extracted = ExtractedUserInput(
        full_name=full_name.strip() if full_name else None,
        dob=dob,
        aadhaar_last4=aadhaar_last4,
        pincode=pincode,
    )

    identity_changed_after_verification = deps.state.verified and any(
        value is not None for value in (full_name, dob, aadhaar_last4, pincode)
    )

    if identity_changed_after_verification:
        return handle_correction(deps, extracted)

    deps.state.merge(extracted)
    return verify_identity(deps)
