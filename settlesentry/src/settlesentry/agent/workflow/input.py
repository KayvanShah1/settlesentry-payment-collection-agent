from __future__ import annotations

from pydantic import ValidationError

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsing.base import ParserContext
from settlesentry.agent.state import ConversationStep, ExtractedUserInput
from settlesentry.agent.workflow.constants import (
    CORRECTABLE_FIELDS,
    CORRECTION_TOKENS,
    SIDE_QUESTION_INTENTS,
)
from settlesentry.agent.workflow.helpers import (
    clear_account_context,
    clear_payment_context,
    clear_payment_secrets,
    result,
    validate_payment_amount,
)
from settlesentry.agent.workflow.result import AgentToolResult
from settlesentry.agent.workflow.routing import (
    expected_fields,
    recommended_node,
    required_fields,
    set_step_from_required_fields,
)
from settlesentry.core import OperationLogContext


def submit_user_input(deps: AgentDeps, user_input: str) -> AgentToolResult:
    # Main input ingestion node: parse, handle side/cancel/correction, merge
    # state, then decide next tool.
    operation = OperationLogContext(operation="submit_user_input")

    if deps.state.completed:
        return result(deps, operation, ok=False, status="conversation_closed")

    current_expected_fields = expected_fields(deps)

    context = ParserContext.from_state(
        deps.state,
        expected_fields=current_expected_fields,
    )

    raw_lower = user_input.lower()
    correction_requested = any(token in raw_lower for token in CORRECTION_TOKENS)

    try:
        extracted = deps.parser.extract(user_input, context=context)

    except ValidationError as exc:
        return result(
            deps,
            operation,
            ok=False,
            status="invalid_user_input",
            required_fields=current_expected_fields,
            facts={"error_type": type(exc).__name__},
        )

    if correction_requested and extracted.intent != UserIntent.CANCEL:
        # Corrections are forced from raw text because LLM/parser outputs may
        # classify them as ordinary field updates.
        extracted = extracted.model_copy(
            update={
                "intent": UserIntent.CORRECT_PREVIOUS_DETAIL,
                "proposed_action": ProposedAction.HANDLE_CORRECTION,
            }
        )

    if extracted.intent == UserIntent.CANCEL or extracted.proposed_action == ProposedAction.CANCEL:
        deps.state.mark_closed()
        clear_payment_secrets(deps)
        return result(deps, operation, ok=True, status="cancelled")

    if extracted.intent in SIDE_QUESTION_INTENTS:
        # Do not merge side-question text into state; return current required
        # fields so the responder can continue the flow.
        facts: dict[str, object] = {}

        if deps.state.verified:
            balance = deps.state.outstanding_balance()
            if balance is not None:
                facts["balance"] = str(balance)

        return result(
            deps,
            operation,
            ok=True,
            status=extracted.intent.value,
            required_fields=required_fields(deps),
            facts=facts,
        )

    if extracted.intent == UserIntent.CORRECT_PREVIOUS_DETAIL:
        return handle_correction(deps, extracted)

    confirmation_received = extracted.confirmation is True
    confirmation_expected = "confirmation" in current_expected_fields

    # Normal user data enters state here. Merge does not clear previous values
    # unless a specific branch does so later. Confirmation is intentionally not
    # merged here because confirm_payment owns the final confirmation flip.
    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if extracted.payment_amount is not None and deps.state.verified:
        # Amount is validated immediately after capture so card details are never
        # collected for an invalid amount.
        blocked = validate_payment_amount(deps, operation)
        if blocked is not None:
            return blocked

    if confirmation_received and confirmation_expected:
        # Confirmation is actionable only when the workflow was already waiting
        # for confirmation. Early "yes" replies must not advance payment.
        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        return result(
            deps,
            operation,
            ok=True,
            status="confirmation_received",
            recommended_tool="confirm_payment",
        )

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=True,
        status="input_captured",
        required_fields=fields,
        recommended_tool=node,
    )


def handle_correction(deps: AgentDeps, extracted: ExtractedUserInput) -> AgentToolResult:
    # Corrections deliberately reset downstream state. Earlier corrected fields
    # can invalidate verification/payment readiness.
    operation = OperationLogContext(operation="handle_correction")

    if deps.state.completed:
        return result(deps, operation, ok=False, status="conversation_closed")

    provided_fields = [field for field in CORRECTABLE_FIELDS if getattr(extracted, field) is not None]

    if not provided_fields:
        return result(
            deps,
            operation,
            ok=True,
            status="correction_requested",
            required_fields=required_fields(deps),
        )

    account_changed = extracted.account_id is not None and extracted.account_id != deps.state.account_id
    identity_changed = any(
        getattr(extracted, field) is not None for field in ("full_name", "dob", "aadhaar_last4", "pincode")
    )
    payment_amount_changed = extracted.payment_amount is not None
    card_changed = any(
        getattr(extracted, field) is not None
        for field in ("cardholder_name", "card_number", "cvv", "expiry_month", "expiry_year")
    )

    if account_changed:
        # Account change invalidates every downstream fact because account data,
        # verification, and payment amount belong to the old account.
        clear_account_context(deps)

    if identity_changed:
        # Identity correction invalidates verification and payment context;
        # amount/card collection must restart after re-verification.
        deps.state.verified = False
        clear_payment_context(deps)

    if payment_amount_changed or card_changed:
        # Payment/card corrections require reconfirmation but do not affect
        # identity verification.
        deps.state.payment_confirmed = False

    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if payment_amount_changed and deps.state.verified:
        blocked = validate_payment_amount(deps, operation)
        if blocked is not None:
            return blocked

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=True,
        status="correction_applied",
        required_fields=fields,
        recommended_tool=node,
        facts={"corrected_fields": provided_fields},
    )
