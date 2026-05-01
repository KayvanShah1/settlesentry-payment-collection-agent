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

PARSER_FIELD_KEYS_BY_EXPECTED_FIELD: dict[str, tuple[str, ...]] = {
    "account_id": ("account_id",),
    "full_name": ("full_name",),
    "dob": ("dob",),
    "aadhaar_last4": ("aadhaar_last4",),
    "pincode": ("pincode",),
    "payment_amount": ("payment_amount",),
    "cardholder_name": ("cardholder_name",),
    "card_number": ("card_number",),
    "cvv": ("cvv",),
    "expiry": ("expiry_month", "expiry_year"),
    "confirmation": ("confirmation",),
}

PARSER_FIELD_KEYS = {
    "account_id",
    "full_name",
    "dob",
    "aadhaar_last4",
    "pincode",
    "payment_amount",
    "cardholder_name",
    "card_number",
    "cvv",
    "expiry_month",
    "expiry_year",
    "confirmation",
}


def keep_only_expected_fields(
    extracted: ExtractedUserInput,
    expected: tuple[str, ...],
) -> ExtractedUserInput:
    if not expected:
        return extracted

    allowed_fields: set[str] = set()

    for field in expected:
        allowed_fields.update(PARSER_FIELD_KEYS_BY_EXPECTED_FIELD.get(field, ()))

    updates = {field: None for field in PARSER_FIELD_KEYS if field not in allowed_fields}

    return extracted.model_copy(update=updates)


def submit_user_input(deps: AgentDeps, user_input: str) -> AgentToolResult:
    """Parse one turn, handle interruptions/corrections, merge state, and route."""
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
        # Raw correction tokens override parser intent to preserve correction flow.
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
        # Side questions must not mutate workflow state.
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

    extracted = keep_only_expected_fields(extracted, current_expected_fields)

    confirmation_received = extracted.confirmation is True
    confirmation_expected = "confirmation" in current_expected_fields

    # Confirmation is handled by confirm_payment, not merge.
    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if extracted.payment_amount is not None and deps.state.verified:
        # Validate amount before collecting card details.
        blocked = validate_payment_amount(deps, operation)
        if blocked is not None:
            return blocked

    if confirmation_received and confirmation_expected:
        # Early "yes" must not advance payment unless confirmation is expected.
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
    """Apply a user correction and reset any invalidated downstream state."""
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
        # Account changes invalidate all downstream verification/payment context.
        clear_account_context(deps)

    if identity_changed:
        # Identity changes require re-verification before payment can continue.
        deps.state.verified = False
        clear_payment_context(deps)

    if payment_amount_changed or card_changed:
        # Payment detail changes always require fresh confirmation.
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
