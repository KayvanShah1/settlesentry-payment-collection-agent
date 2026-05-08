from __future__ import annotations

from decimal import Decimal

from pydantic_ai import FunctionToolset, RunContext

from settlesentry.agent.autonomous.tools.common import (
    log_tool_call,
    tool_options,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.agent.workflow.helpers import clear_card_details
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
Use amount tools only after identity has been verified and the customer provides a payment amount.

Amount handling:
- submit only the payment amount
- do not treat verification factors, Aadhaar digits, pincode, CVV, expiry, or card numbers as payment amounts
- do not accept an amount from earlier turns unless it is still present in safe state or tool context
- if amount validation fails, ask only for a corrected amount

The amount tool validates policy limits and outstanding-balance constraints.
""".strip()


CARD_TOOL_INSTRUCTIONS = """
Use card tools only after a valid payment amount has been accepted.

Card handling:
- submit any provided cardholder name, card number, expiry, or CVV
- partial card submission is allowed
- parse expiry in MM/YYYY into expiry_month and expiry_year
- never expose full card number or CVV back to the customer
- never treat card submission as payment confirmation
- if a payment attempt fails because card details are invalid, all card details are cleared; ask for cardholder name, full card number, expiry in MM/YYYY format, and CVV again

Card tools only collect or validate card details. They must not process payment.
""".strip()


PREPARE_CONFIRMATION_TOOL_INSTRUCTIONS = """
Use the preparation tool only after payment amount and required card details are complete.

Preparation handling:
- prepare payment only when complete payment details are ready for explicit confirmation
- do not process payment during preparation
- do not infer confirmation from card details, amount entry, silence, or ambiguous replies

Only claim payment readiness when the tool returns payment_ready_for_confirmation.
""".strip()


FINAL_CONFIRMATION_TOOL_INSTRUCTIONS = """
Use final confirmation tools only when explicit confirmation handling is pending.

Final confirmation handling:
- process payment only when the customer explicitly confirms with yes or equivalent clear approval
- decline payment when the customer says no, cancel, stop, or refuses confirmation
- do not infer confirmation from card details, amount entry, silence, or ambiguous replies
- if payment processing returns invalid_card, invalid_cvv, or invalid_expiry, do not ask only for the failed field; ask for complete card details again because the card detail bundle has been cleared

Only claim payment success if the tool returns payment_success or conversation_closed with transaction_id.
If processing fails or attempts are exhausted, do not retry automatically unless the tool explicitly keeps the flow open.
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


prepare_confirmation_toolset = FunctionToolset(
    instructions=PREPARE_CONFIRMATION_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@prepare_confirmation_toolset.tool(
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


final_confirmation_toolset = FunctionToolset(
    instructions=FINAL_CONFIRMATION_TOOL_INSTRUCTIONS,
    include_return_schema=True,
    sequential=True,
)


@final_confirmation_toolset.tool(
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


@final_confirmation_toolset.tool(
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
    clear_card_details(deps)

    return workflow_result(
        deps,
        operation,
        ok=True,
        status="cancelled",
    )


@final_confirmation_toolset.tool(
    name="correct_payment_amount",
    **tool_options(
        description=(
            "Correct the payment amount after payment details have been collected "
            "and re-stage the payment for confirmation."
        ),
        category="payment_amount",
        sensitivity="medium",
        mutates_state=True,
    ),
)
@log_tool_call(tool_name="correct_payment_amount", category="payment_amount")
def correct_payment_amount(
    ctx: RunContext[AgentDeps],
    amount: Decimal,
) -> object:
    amount_result = capture_payment_amount(
        ctx.deps,
        ExtractedUserInput(payment_amount=amount),
    )

    if not amount_result.ok:
        return amount_result

    if amount_result.recommended_tool == "prepare_payment":
        return prepare_payment(ctx.deps)

    return amount_result
