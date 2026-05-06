from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import log_tool_call, tool_options
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.input import handle_correction
from settlesentry.agent.workflow.operations import verify_identity

IDENTITY_TOOL_INSTRUCTIONS = """
Use identity tools when the customer provides full name, DOB, Aadhaar last 4 digits, or pincode during identity verification.

Call provide_identity_details whenever any identity detail is provided:
- full name alone is allowed
- secondary factor alone is allowed if full name is already stored
- full name plus secondary factor together is allowed
- corrected identity details must be submitted through the tool
- when required_fields includes dob_or_aadhaar_last4_or_pincode, treat bare values as actionable:
  - YYYY-MM-DD -> dob
  - 4 digits -> aadhaar_last4
  - 6 digits -> pincode
- do not ask again when the customer provides one of those values; submit it through the tool

Full name alone is not enough to verify identity. One secondary factor is required.
The tool result is the only source of truth for verification.

On identity failure:
- do not reveal which field was incorrect
- do not reveal balance
- follow required_fields from the tool result

If verification is exhausted, stop identity and payment collection.
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
@log_tool_call(tool_name="provide_identity_details", category="identity_verification")
def provide_identity_details(
    ctx: RunContext[AgentDeps],
    full_name: Annotated[str | None, Field(description="Customer full name exactly as registered.")] = None,
    dob: Annotated[str | None, Field(description="Date of birth in YYYY-MM-DD format.")] = None,
    aadhaar_last4: Annotated[str | None, Field(description="Last 4 digits of Aadhaar.")] = None,
    pincode: Annotated[str | None, Field(description="6-digit account pincode.")] = None,
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
