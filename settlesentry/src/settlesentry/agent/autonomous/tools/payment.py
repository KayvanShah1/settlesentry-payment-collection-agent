from __future__ import annotations

from decimal import Decimal

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import (
    log_tool_call,
    tool_options,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.helpers import clear_payment_secrets
from settlesentry.agent.workflow.helpers import result as workflow_result
from settlesentry.agent.workflow.operations import (
    capture_card_details,
    capture_payment_amount,
    prepare_payment,
    process_payment,
    recap_and_close,
)
from settlesentry.agent.workflow.operations import confirm_payment as confirm_payment_operation
from settlesentry.core import OperationLogContext

AMOUNT_TOOL_INSTRUCTIONS = """
Use amount tools after identity verification when the user provides the amount to pay.

Do not treat verification factors as payment amounts.
If amount validation fails, ask only for a corrected amount.
""".strip()


CARD_TOOL_INSTRUCTIONS = """
Use card tools after a valid payment amount has been accepted.

Collect cardholder name, card number, expiry, and CVV.
Never expose full card number or CVV back to the user.
When card details are complete, prepare payment for confirmation.
""".strip()


CONFIRMATION_TOOL_INSTRUCTIONS = """
Use confirmation tools to prepare, confirm, process, decline, or close payment.

Prepare payment only after amount and card details are complete.
Process payment only after explicit user confirmation.
Only claim success if the tool returns payment_success or conversation_closed with transaction_id.
""".strip()


amount_toolset = FunctionToolset(
    instructions=AMOUNT_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@amount_toolset.tool(
    name="provide_payment_amount",
    **tool_options(
        description="Submit and validate the INR payment amount.",
        category="payment_amount",
        sensitivity="medium",
        mutates_state=True,
    ),
)
@log_tool_call(tool_name="provide_payment_amount", category="payment_amount")
def provide_payment_amount(
    ctx: RunContext[AgentDeps],
    amount: Decimal,
) -> object:
    extracted = ExtractedUserInput(payment_amount=amount)
    return capture_payment_amount(ctx.deps, extracted)


card_toolset = FunctionToolset(
    instructions=CARD_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@card_toolset.tool(
    name="provide_card_details",
    **tool_options(
        description="Submit partial or complete card details.",
        category="card_details",
        sensitivity="critical",
        mutates_state=True,
    ),
)
@log_tool_call(tool_name="provide_card_details", category="card_details")
def provide_card_details(
    ctx: RunContext[AgentDeps],
    cardholder_name: str | None = None,
    card_number: str | None = None,
    expiry_month: int | None = None,
    expiry_year: int | None = None,
    cvv: str | None = None,
) -> object:
    extracted = ExtractedUserInput(
        cardholder_name=cardholder_name.strip() if cardholder_name else None,
        card_number=card_number,
        expiry_month=expiry_month,
        expiry_year=expiry_year,
        cvv=cvv,
    )

    return capture_card_details(ctx.deps, extracted)


confirmation_toolset = FunctionToolset(
    instructions=CONFIRMATION_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@confirmation_toolset.tool(
    name="prepare_payment_for_confirmation",
    **tool_options(
        description="Stage complete payment details for explicit confirmation.",
        category="payment_confirmation",
        sensitivity="high",
        mutates_state=True,
    ),
)
@log_tool_call(tool_name="prepare_payment_for_confirmation", category="payment_confirmation")
def prepare_payment_for_confirmation(ctx: RunContext[AgentDeps]) -> object:
    return prepare_payment(ctx.deps)


@confirmation_toolset.tool(
    name="confirm_and_process_payment",
    **tool_options(
        description="Confirm and process the prepared payment.",
        category="payment_processing",
        sensitivity="critical",
        timeout=12.0,
        mutates_state=True,
        calls_external_api=True,
        moves_money=True,
    ),
)
@log_tool_call(tool_name="confirm_and_process_payment", category="payment_processing")
def confirm_and_process_payment(ctx: RunContext[AgentDeps]) -> object:
    deps = ctx.deps

    confirmed = confirm_payment_operation(deps, confirmed=True)

    if not confirmed.ok:
        return confirmed

    if confirmed.recommended_tool != "process_payment":
        return confirmed

    payment_result = process_payment(deps)

    if payment_result.ok and payment_result.recommended_tool == "recap_and_close":
        return recap_and_close(deps)

    return payment_result


@confirmation_toolset.tool(
    name="decline_payment",
    **tool_options(
        description="Decline the prepared payment and close safely.",
        category="payment_processing",
        sensitivity="medium",
        timeout=3.0,
        mutates_state=True,
        terminal=True,
    ),
)
@log_tool_call(tool_name="decline_payment", category="payment_processing")
def decline_payment(ctx: RunContext[AgentDeps]) -> object:
    deps = ctx.deps
    operation = OperationLogContext(operation="decline_payment")

    deps.state.payment_confirmed = False
    deps.state.mark_closed()
    clear_payment_secrets(deps)

    return workflow_result(
        deps,
        operation,
        ok=True,
        status="cancelled",
    )
